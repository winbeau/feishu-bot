import json

import pytest
from starlette.requests import Request

from app.core.models import MessageType, PlatformType, UnifiedMessage
from app.platforms.feishu import FeishuAdapter


def make_request(payload: dict) -> Request:
    body = json.dumps(payload).encode()

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/feishu/webhook",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


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
        return FakeResponse({"code": 0, "data": {"message_id": "sent-message"}})


@pytest.fixture
def feishu_event() -> dict:
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user_1"}},
            "message": {
                "message_id": "om_message_1",
                "chat_id": "oc_chat_1",
                "message_type": "text",
                "content": json.dumps({"text": "hello feishu"}),
            },
        },
    }


async def test_feishu_handle_challenge_returns_challenge_when_token_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    adapter = FeishuAdapter()
    request = make_request(
        {
            "type": "url_verification",
            "token": "expected-token",
            "challenge": "challenge-code",
        }
    )

    response = await adapter.handle_challenge(request)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {"challenge": "challenge-code"}


async def test_feishu_handle_challenge_returns_401_when_token_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    adapter = FeishuAdapter()
    request = make_request(
        {
            "type": "url_verification",
            "token": "wrong-token",
            "challenge": "challenge-code",
        }
    )

    response = await adapter.handle_challenge(request)

    assert response is not None
    assert response.status_code == 401


async def test_feishu_verify_signature_returns_false_when_token_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    adapter = FeishuAdapter()
    request = make_request({"token": "wrong-token"})

    assert await adapter.verify_signature(request) is False


async def test_feishu_parse_incoming_text_event(feishu_event: dict) -> None:
    adapter = FeishuAdapter()

    message = await adapter.parse_incoming(feishu_event)

    assert message == UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="oc_chat_1",
        user_id="ou_user_1",
        content="hello feishu",
        message_id="om_message_1",
        raw=feishu_event,
    )


async def test_feishu_send_message_gets_token_and_sends_text_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient()
    adapter = FeishuAdapter(http_client=http_client)
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="oc_chat_1",
        user_id="ou_user_1",
        content="reply text",
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert len(http_client.calls) == 2
    token_url, token_kwargs = http_client.calls[0]
    assert token_url == (
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    )
    assert token_kwargs["json"] == {"app_id": "app-id", "app_secret": "app-secret"}
    send_url, send_kwargs = http_client.calls[1]
    assert send_url == (
        "https://open.feishu.cn/open-apis/im/v1/messages"
        "?receive_id_type=chat_id"
    )
    assert send_kwargs["headers"]["Authorization"] == "Bearer tenant-token"
    assert send_kwargs["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "reply text"}, ensure_ascii=False),
    }
