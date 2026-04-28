import logging

import httpx
import pytest

from app.core.models import Attachment
from app.services.dify_files import (
    DifyFilePermissionError,
    DifyFileTooLargeError,
    DifyFileUnsupportedError,
    DifyFileUploadError,
    DifyFileUploadService,
)


class FakeResponse:
    def __init__(self, payload: dict | None = None, *, status_code: int = 200) -> None:
        self._payload = payload or {}
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


class FakeHTTPClient:
    def __init__(self, result: FakeResponse | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, **kwargs) -> FakeResponse:
        file_tuple = kwargs["files"]["file"]
        file_name, handle, mime_type = file_tuple
        self.calls.append(
            (
                url,
                {
                    **kwargs,
                    "files": {
                        "file": (
                            file_name,
                            handle.read(),
                            mime_type,
                        )
                    },
                },
            )
        )
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


async def test_dify_file_upload_posts_multipart_and_updates_attachment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "secret-key")
    path = tmp_path / "image.png"
    path.write_bytes(b"image-bytes")
    client = FakeHTTPClient(FakeResponse({"id": "upload-id-1"}))
    service = DifyFileUploadService(
        http_client=client,
        base_url="https://dify.example.test",
    )
    attachment = Attachment(
        file_name="image.png",
        mime_type="image/png",
        local_path=str(path),
    )

    result = await service.upload_attachment(
        attachment,
        user_id="user-1",
        dify_file_type="image",
    )

    assert result is attachment
    assert attachment.dify_upload_file_id == "upload-id-1"
    assert attachment.dify_file_type == "image"
    assert client.calls == [
        (
            "https://dify.example.test/files/upload",
            {
                "headers": {"Authorization": "Bearer secret-key"},
                "data": {"user": "user-1"},
                "files": {"file": ("image.png", b"image-bytes", "image/png")},
                "timeout": 30.0,
            },
        )
    ]


async def test_dify_file_upload_adds_image_extension_when_file_name_has_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "secret-key")
    path = tmp_path / "img_v3_key"
    path.write_bytes(b"image-bytes")
    client = FakeHTTPClient(FakeResponse({"id": "upload-id-1"}))
    service = DifyFileUploadService(
        http_client=client,
        base_url="https://dify.example.test",
    )

    await service.upload_attachment(
        Attachment(
            local_path=str(path),
            mime_type="image/png; charset=binary",
        ),
        user_id="user-1",
        dify_file_type="image",
    )

    assert client.calls[0][1]["files"] == {
        "file": ("img_v3_key.png", b"image-bytes", "image/png; charset=binary")
    }


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, DifyFilePermissionError),
        (403, DifyFilePermissionError),
        (413, DifyFileTooLargeError),
        (415, DifyFileUnsupportedError),
    ],
)
async def test_dify_file_upload_maps_expected_status_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    status_code: int,
    expected_error: type[DifyFileUploadError],
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "secret-key")
    path = tmp_path / "image.png"
    path.write_bytes(b"image-bytes")
    client = FakeHTTPClient(FakeResponse(status_code=status_code))
    service = DifyFileUploadService(
        http_client=client,
        base_url="https://dify.example.test",
    )

    with pytest.raises(expected_error):
        await service.upload_attachment(
            Attachment(local_path=str(path)),
            user_id="user-1",
            dify_file_type="image",
        )


async def test_dify_file_upload_wraps_network_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "secret-key")
    path = tmp_path / "image.png"
    path.write_bytes(b"image-bytes")
    client = FakeHTTPClient(httpx.ConnectError("network unavailable"))
    service = DifyFileUploadService(
        http_client=client,
        base_url="https://dify.example.test",
    )

    with pytest.raises(DifyFileUploadError):
        await service.upload_attachment(
            Attachment(local_path=str(path)),
            user_id="user-1",
            dify_file_type="image",
        )


async def test_dify_file_upload_logs_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "secret-key")
    path = tmp_path / "image.png"
    path.write_bytes(b"image-bytes")
    client = FakeHTTPClient(FakeResponse({"id": "upload-id-1"}))
    service = DifyFileUploadService(
        http_client=client,
        base_url="https://dify.example.test",
    )

    with caplog.at_level(logging.INFO):
        await service.upload_attachment(
            Attachment(
                file_name="image.png",
                mime_type="image/png",
                local_path=str(path),
            ),
            user_id="user-1",
            dify_file_type="image",
        )

    assert "secret-key" not in caplog.text
    assert "dify file uploaded" in caplog.text
