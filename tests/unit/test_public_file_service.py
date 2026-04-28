import re

import pytest

from app.core.models import Attachment
from app.services.public_files import (
    PublicFilePublishError,
    PublicFileService,
    PublicFileUrlValidator,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


class FakeHTTPResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {
            "content-type": "image/png",
            "content-length": "123",
        }


class FakeHTTPClient:
    def __init__(self, *, head_response: FakeHTTPResponse) -> None:
        self.head_response = head_response
        self.head_calls = []
        self.get_calls = []

    async def head(self, url: str, **kwargs):
        self.head_calls.append((url, kwargs))
        return self.head_response

    async def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeHTTPResponse(headers={"content-type": "image/jpeg"})


def test_public_file_service_publishes_image_with_uuid_name(tmp_path) -> None:
    source = tmp_path / "feishu-image"
    source.write_bytes(PNG_BYTES)
    public_dir = tmp_path / "public"
    attachment = Attachment(file_key="img-key", local_path=str(source))
    service = PublicFileService(
        base_url="https://bot.example.test",
        public_dir=public_dir,
    )

    result = service.publish_image(attachment)

    assert result is attachment
    assert result.url is not None
    assert re.fullmatch(
        r"https://bot\.example\.test/public/files/[0-9a-f]{32}\.png",
        result.url,
    )
    public_name = result.url.rsplit("/", 1)[1]
    assert public_name != "img-key.png"
    assert (public_dir / public_name).read_bytes() == PNG_BYTES


def test_public_file_service_uses_mime_type_when_magic_bytes_are_unavailable(
    tmp_path,
) -> None:
    source = tmp_path / "downloaded"
    source.write_bytes(b"not enough image header")
    service = PublicFileService(
        base_url="http://120.46.94.148",
        public_dir=tmp_path / "public",
    )
    attachment = Attachment(
        local_path=str(source),
        mime_type="image/webp; charset=binary",
    )

    service.publish_image(attachment)

    assert attachment.url is not None
    assert attachment.url.endswith(".webp")


def test_public_file_service_requires_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("PUBLIC_FILE_BASE_URL", raising=False)
    source = tmp_path / "image.png"
    source.write_bytes(PNG_BYTES)
    service = PublicFileService(base_url="", public_dir=tmp_path / "public")

    with pytest.raises(PublicFilePublishError, match="PUBLIC_FILE_BASE_URL"):
        service.publish_image(Attachment(local_path=str(source)))


def test_public_file_service_rejects_missing_source_file(tmp_path) -> None:
    service = PublicFileService(
        base_url="https://bot.example.test",
        public_dir=tmp_path / "public",
    )

    with pytest.raises(PublicFilePublishError, match="does not exist"):
        service.publish_image(Attachment(local_path=str(tmp_path / "missing.png")))


def test_public_file_service_rejects_unrecognized_image_type(tmp_path) -> None:
    source = tmp_path / "downloaded.bin"
    source.write_bytes(b"plain text")
    service = PublicFileService(
        base_url="https://bot.example.test",
        public_dir=tmp_path / "public",
    )

    with pytest.raises(PublicFilePublishError, match="unrecognized image type"):
        service.publish_image(Attachment(local_path=str(source)))


async def test_public_file_url_validator_accepts_reachable_image_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    http_client = FakeHTTPClient(head_response=FakeHTTPResponse())
    validator = PublicFileUrlValidator(http_client=http_client)

    await validator.validate_image_url("https://bot.example.test/public/files/img.png")

    assert http_client.head_calls[0][0] == (
        "https://bot.example.test/public/files/img.png"
    )
    assert http_client.get_calls == []
    assert "public_url=https://bot.example.test/public/files/img.png" in caplog.text
    assert "status_code=200" in caplog.text
    assert "content_type=image/png" in caplog.text


async def test_public_file_url_validator_falls_back_to_get_when_head_is_unsupported() -> None:
    http_client = FakeHTTPClient(head_response=FakeHTTPResponse(status_code=405))
    validator = PublicFileUrlValidator(http_client=http_client)

    await validator.validate_image_url("https://bot.example.test/public/files/img.jpg")

    assert http_client.head_calls
    assert http_client.get_calls[0][0] == "https://bot.example.test/public/files/img.jpg"
    assert http_client.get_calls[0][1]["headers"] == {"Range": "bytes=0-0"}


async def test_public_file_url_validator_rejects_non_2xx_status() -> None:
    http_client = FakeHTTPClient(
        head_response=FakeHTTPResponse(status_code=404),
    )
    validator = PublicFileUrlValidator(http_client=http_client)

    with pytest.raises(PublicFilePublishError, match="status 404"):
        await validator.validate_image_url(
            "https://bot.example.test/public/files/missing.png"
        )


async def test_public_file_url_validator_rejects_non_image_content_type() -> None:
    http_client = FakeHTTPClient(
        head_response=FakeHTTPResponse(headers={"content-type": "application/json"}),
    )
    validator = PublicFileUrlValidator(http_client=http_client)

    with pytest.raises(PublicFilePublishError, match="non-image content type"):
        await validator.validate_image_url(
            "https://bot.example.test/public/files/not-image.png"
        )
