import json

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.models import UnifiedMessage
from app.main import app, get_gateway, _public_file_base_url_from_request
from app.platforms.feishu import FeishuAdapter
from app.services.feishu_files import FeishuFileDownloadError
from app.services.public_files import PublicFilePublishError


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[UnifiedMessage] = []

    async def route(self, message: UnifiedMessage) -> str:
        self.messages.append(message)
        return "gateway reply"


class FakeDeduplicationStore:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.message_ids: list[str] = []

    async def mark_seen(self, message_id: str) -> bool:
        self.message_ids.append(message_id)
        if message_id in self.seen:
            return False

        self.seen.add(message_id)
        return True


class FakeSessionStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.sessions: dict[tuple[str, str], str] = {}

    async def get_or_create_session_id(self, platform, user_id: str) -> str:
        platform_value = platform.value if hasattr(platform, "value") else platform
        self.calls.append((platform_value, user_id))
        return self.sessions.setdefault((platform_value, user_id), "dify-session-1")


class FakeFileService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    async def download_attachment(self, message_id, attachment, file_type):
        self.calls.append((message_id, attachment.file_key, file_type))
        if self.fail:
            raise FeishuFileDownloadError("boom")
        attachment.local_path = (
            "/tmp/downloaded-image.png"
            if file_type == "image"
            else "/tmp/downloaded.csv"
        )
        return attachment


class FakePublicFileService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def publish_image(self, attachment):
        self.calls.append(attachment.file_key)
        if self.fail:
            raise PublicFilePublishError("boom")
        attachment.url = f"https://bot.example.test/public/files/{attachment.file_key}.png"
        return attachment


class FakeParserService:
    def __init__(self) -> None:
        self.calls = []

    def parse_attachment(self, attachment):
        self.calls.append(attachment)
        attachment.parsed_text = "parsed csv"
        attachment.file_tags.append("parsed")
        return attachment


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeHTTPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs))
        if url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        return FakeResponse({"code": 0})


def test_get_gateway_builds_default_production_gateway() -> None:
    existing = getattr(app.state, "gateway", None)
    if existing is not None:
        del app.state.gateway

    try:
        gateway = get_gateway()

        assert gateway is app.state.gateway
    finally:
        if hasattr(app.state, "gateway"):
            del app.state.gateway
        if existing is not None:
            app.state.gateway = existing


def test_public_file_base_url_prefers_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_FILE_BASE_URL", "https://configured.example.test")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/feishu/webhook",
            "headers": [(b"host", b"runtime.example.test")],
            "scheme": "http",
            "server": ("runtime.example.test", 80),
            "client": ("127.0.0.1", 12345),
        }
    )

    assert _public_file_base_url_from_request(request) is None


def test_public_file_base_url_falls_back_to_forwarded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_FILE_BASE_URL", raising=False)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/feishu/webhook",
            "headers": [
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-host", b"bot.example.test"),
            ],
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )

    assert _public_file_base_url_from_request(request) == "https://bot.example.test"


@pytest.fixture
def feishu_payload() -> dict:
    return {
        "schema": "2.0",
        "token": "expected-token",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "message_id": "om_message_1",
                "chat_id": "oc_chat_1",
                "message_type": "text",
                "content": json.dumps({"text": "incoming text"}),
            },
        },
    }


def test_feishu_webhook_routes_message_and_sends_reply(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    deduplication_store = FakeDeduplicationStore()
    session_store = FakeSessionStore()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = deduplication_store
    app.state.session_store = session_store

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(gateway.messages) == 1
    assert gateway.messages[0].content == "incoming text"
    assert gateway.messages[0].session_id == "dify-session-1"
    assert len(http_client.calls) == 2
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "gateway reply"}, ensure_ascii=False),
    }
    assert deduplication_store.message_ids == ["om_message_1"]
    assert session_store.calls == [("feishu", "ou_user_1")]


def test_feishu_webhook_deduplicates_retried_message(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    deduplication_store = FakeDeduplicationStore()
    session_store = FakeSessionStore()
    file_service = FakeFileService()
    parser_service = FakeParserService()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = deduplication_store
    app.state.session_store = session_store
    app.state.feishu_file_service = file_service
    app.state.file_parser_service = parser_service

    try:
        first_response = test_client.post("/feishu/webhook", json=feishu_payload)
        second_response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service

    assert first_response.status_code == 200
    assert first_response.json() == {"ok": True}
    assert second_response.status_code == 200
    assert second_response.json() == {"ok": True}
    assert len(gateway.messages) == 1
    assert len(http_client.calls) == 2
    assert deduplication_store.message_ids == ["om_message_1", "om_message_1"]
    assert file_service.calls == []
    assert parser_service.calls == []


def test_feishu_webhook_deduplicates_retried_image_before_file_work(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "image"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {"image_key": "image-key"}
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    deduplication_store = FakeDeduplicationStore()
    session_store = FakeSessionStore()
    file_service = FakeFileService()
    public_file_service = FakePublicFileService()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = deduplication_store
    app.state.session_store = session_store
    app.state.feishu_file_service = file_service
    app.state.file_parser_service = FakeParserService()
    app.state.public_file_service = public_file_service

    try:
        first_response = test_client.post("/feishu/webhook", json=feishu_payload)
        second_response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert len(gateway.messages) == 1
    assert file_service.calls == [("om_message_1", "image-key", "image")]
    assert public_file_service.calls == ["image-key"]
    assert deduplication_store.message_ids == ["om_message_1", "om_message_1"]


def test_feishu_webhook_downloads_and_parses_file_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "file"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {
            "file_key": "file-key",
            "file_name": "data.csv",
            "mime_type": "text/csv",
            "size": 10,
        }
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    file_service = FakeFileService()
    parser_service = FakeParserService()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = FakeDeduplicationStore()
    app.state.session_store = FakeSessionStore()
    app.state.feishu_file_service = file_service
    app.state.file_parser_service = parser_service
    app.state.public_file_service = FakePublicFileService()

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert response.status_code == 200
    assert len(gateway.messages) == 1
    attachment = gateway.messages[0].attachments[0]
    assert file_service.calls == [("om_message_1", "file-key", "file")]
    assert parser_service.calls == [attachment]
    assert attachment.parsed_text == "parsed csv"
    assert attachment.file_tags == ["parsed"]


def test_feishu_webhook_replies_fixed_message_when_download_fails(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "image"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {"image_key": "image-key"}
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = FakeDeduplicationStore()
    app.state.session_store = FakeSessionStore()
    app.state.feishu_file_service = FakeFileService(fail=True)
    app.state.file_parser_service = FakeParserService()
    app.state.public_file_service = FakePublicFileService()

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert response.status_code == 200
    assert gateway.messages == []
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps(
            {"text": "文件下载失败，请稍后重试"},
            ensure_ascii=False,
        ),
    }


def test_feishu_webhook_publishes_image_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "image"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {"image_key": "image-key"}
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    file_service = FakeFileService()
    public_file_service = FakePublicFileService()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = FakeDeduplicationStore()
    app.state.session_store = FakeSessionStore()
    app.state.feishu_file_service = file_service
    app.state.file_parser_service = FakeParserService()
    app.state.public_file_service = public_file_service

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert response.status_code == 200
    assert len(gateway.messages) == 1
    attachment = gateway.messages[0].attachments[0]
    assert file_service.calls == [("om_message_1", "image-key", "image")]
    assert public_file_service.calls == ["image-key"]
    assert attachment.local_path == "/tmp/downloaded-image.png"
    assert attachment.url == "https://bot.example.test/public/files/image-key.png"
    assert attachment.dify_upload_file_id is None
    assert attachment.dify_file_type is None


def test_feishu_webhook_publishes_post_image_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "post"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {
            "title": "post title",
            "content": [
                [
                    {"tag": "text", "text": "caption"},
                    {"tag": "img", "image_key": "post-image-key"},
                ]
            ],
        }
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    file_service = FakeFileService()
    public_file_service = FakePublicFileService()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = FakeDeduplicationStore()
    app.state.session_store = FakeSessionStore()
    app.state.feishu_file_service = file_service
    app.state.file_parser_service = FakeParserService()
    app.state.public_file_service = public_file_service

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert response.status_code == 200
    assert len(gateway.messages) == 1
    assert gateway.messages[0].content == "post title\ncaption"
    assert file_service.calls == [("om_message_1", "post-image-key", "image")]
    assert public_file_service.calls == ["post-image-key"]
    assert (
        gateway.messages[0].attachments[0].url
        == "https://bot.example.test/public/files/post-image-key.png"
    )


def test_feishu_webhook_replies_fixed_message_when_image_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
    test_client: TestClient,
    feishu_payload: dict,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    feishu_payload["event"]["message"]["message_type"] = "image"
    feishu_payload["event"]["message"]["content"] = json.dumps(
        {"image_key": "image-key"}
    )
    http_client = FakeHTTPClient()
    gateway = FakeGateway()
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = FakeDeduplicationStore()
    app.state.session_store = FakeSessionStore()
    app.state.feishu_file_service = FakeFileService()
    app.state.file_parser_service = FakeParserService()
    app.state.public_file_service = FakePublicFileService(fail=True)

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store
        del app.state.session_store
        del app.state.feishu_file_service
        del app.state.file_parser_service
        del app.state.public_file_service

    assert response.status_code == 200
    assert gateway.messages == []
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps(
            {"text": "图片处理失败，请稍后重试"},
            ensure_ascii=False,
        ),
    }
