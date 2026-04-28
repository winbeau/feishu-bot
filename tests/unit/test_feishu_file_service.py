import httpx
import pytest

from app.core.models import Attachment
from app.services.feishu_files import (
    FeishuFileDownloadError,
    FeishuFileNotFoundError,
    FeishuFilePermissionError,
    FeishuFileService,
)


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        status_code: int = 200,
        content: bytes = b"file-bytes",
        headers: dict | None = None,
    ) -> None:
        self._payload = payload or {"code": 0, "tenant_access_token": "tenant-token"}
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://open.feishu.cn/token")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("bad status", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class FakeHTTPClient:
    def __init__(
        self,
        *,
        get_response: FakeResponse | BaseException | None = None,
    ) -> None:
        self.get_response = get_response or FakeResponse(content=b"downloaded")
        self.calls: list[tuple[str, str, dict]] = []

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if isinstance(self.get_response, BaseException):
            raise self.get_response
        return self.get_response


async def test_feishu_file_service_downloads_file_to_local_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient()
    http_client.get_response.headers = {"content-type": "image/png; charset=binary"}
    service = FeishuFileService(http_client=http_client, download_dir=tmp_path)
    attachment = Attachment(file_key="file-key", file_name="report.csv")

    result = await service.download_attachment("message-id", attachment, "file")

    assert result.local_path == str(tmp_path / "file-key_report.csv")
    assert result.mime_type == "image/png"
    assert (tmp_path / "file-key_report.csv").read_bytes() == b"downloaded"
    assert http_client.calls[1][1] == (
        "https://open.feishu.cn/open-apis/im/v1/messages/message-id"
        "/resources/file-key?type=file"
    )
    assert http_client.calls[1][2]["headers"] == {
        "Authorization": "Bearer tenant-token"
    }


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, FeishuFilePermissionError),
        (403, FeishuFilePermissionError),
        (404, FeishuFileNotFoundError),
        (500, FeishuFileDownloadError),
    ],
)
async def test_feishu_file_service_maps_download_status_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    status_code: int,
    expected_error: type[Exception],
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    service = FeishuFileService(
        http_client=FakeHTTPClient(get_response=FakeResponse(status_code=status_code)),
        download_dir=tmp_path,
    )

    with pytest.raises(expected_error):
        await service.download_attachment("message-id", Attachment(file_key="key"), "file")


async def test_feishu_file_service_wraps_network_error_and_logs_no_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "super-secret")
    service = FeishuFileService(
        http_client=FakeHTTPClient(get_response=httpx.ConnectError("offline")),
        download_dir=tmp_path,
    )

    with pytest.raises(FeishuFileDownloadError):
        await service.download_attachment(
            "message-id",
            Attachment(file_key="key"),
            "image",
        )

    assert "super-secret" not in caplog.text
    assert "tenant-token" not in caplog.text
