import re

import pytest

from app.core.models import Attachment
from app.services.public_files import PublicFilePublishError, PublicFileService


PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


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
