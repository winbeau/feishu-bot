import json

import pytest
from fastapi.testclient import TestClient

from app.core.models import UnifiedMessage
from app.main import app
from app.platforms.feishu import FeishuAdapter


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
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = deduplication_store

    try:
        response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(gateway.messages) == 1
    assert gateway.messages[0].content == "incoming text"
    assert gateway.messages[0].session_id == "oc_chat_1"
    assert len(http_client.calls) == 2
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "gateway reply"}, ensure_ascii=False),
    }
    assert deduplication_store.message_ids == ["om_message_1"]


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
    app.state.feishu_adapter = FeishuAdapter(http_client=http_client)
    app.state.gateway = gateway
    app.state.deduplication_store = deduplication_store

    try:
        first_response = test_client.post("/feishu/webhook", json=feishu_payload)
        second_response = test_client.post("/feishu/webhook", json=feishu_payload)
    finally:
        del app.state.feishu_adapter
        del app.state.gateway
        del app.state.deduplication_store

    assert first_response.status_code == 200
    assert first_response.json() == {"ok": True}
    assert second_response.status_code == 200
    assert second_response.json() == {"ok": True}
    assert len(gateway.messages) == 1
    assert len(http_client.calls) == 2
    assert deduplication_store.message_ids == ["om_message_1", "om_message_1"]
