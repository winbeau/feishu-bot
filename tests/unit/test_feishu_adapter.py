import json

import httpx
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
    def __init__(self, send_responses: list[dict | Exception] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._send_responses = send_responses or [
            {"code": 0, "data": {"message_id": "sent-message"}}
        ]

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs))
        if url.endswith("/open-apis/auth/v3/tenant_access_token/internal"):
            return FakeResponse({"code": 0, "tenant_access_token": "tenant-token"})
        send_response = self._send_responses.pop(0)
        if isinstance(send_response, Exception):
            raise send_response
        return FakeResponse(send_response)


def assert_post_payload(payload: dict, *, receive_id: str, text: str) -> None:
    assert payload["receive_id"] == receive_id
    assert payload["msg_type"] == "post"
    content = json.loads(payload["content"])
    assert content == {
        "zh_cn": {
            "title": "",
            "content": [[{"tag": "md", "text": text}]],
        }
    }


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


async def test_feishu_verify_signature_accepts_v2_header_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_VERIFICATION_TOKEN", "expected-token")
    adapter = FeishuAdapter()
    request = make_request({"header": {"token": "expected-token"}})

    assert await adapter.verify_signature(request) is True


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


async def test_feishu_parse_incoming_image_event(feishu_event: dict) -> None:
    feishu_event["event"]["message"]["message_type"] = "image"
    feishu_event["event"]["message"]["content"] = json.dumps(
        {"image_key": "img_v3_key"}
    )
    adapter = FeishuAdapter()

    message = await adapter.parse_incoming(feishu_event)

    assert message.message_type is MessageType.IMAGE
    assert message.message_id == "om_message_1"
    assert message.session_id == "oc_chat_1"
    assert message.user_id == "ou_user_1"
    assert message.content == ""
    assert len(message.attachments) == 1
    assert message.attachments[0].file_key == "img_v3_key"


async def test_feishu_parse_incoming_file_event(feishu_event: dict) -> None:
    feishu_event["event"]["message"]["message_type"] = "file"
    feishu_event["event"]["message"]["content"] = json.dumps(
        {
            "file_key": "file_v3_key",
            "file_name": "report.csv",
            "mime_type": "text/csv",
            "size": 123,
        }
    )
    adapter = FeishuAdapter()

    message = await adapter.parse_incoming(feishu_event)

    assert message.message_type is MessageType.FILE
    assert message.attachments[0].file_key == "file_v3_key"
    assert message.attachments[0].file_name == "report.csv"
    assert message.attachments[0].mime_type == "text/csv"
    assert message.attachments[0].size == 123


async def test_feishu_parse_incoming_post_event_with_image(feishu_event: dict) -> None:
    feishu_event["event"]["message"]["message_type"] = "post"
    feishu_event["event"]["message"]["content"] = json.dumps(
        {
            "title": "image post",
            "content": [
                [
                    {"tag": "text", "text": "caption"},
                    {"tag": "img", "image_key": "post_image_key"},
                ]
            ],
        }
    )
    adapter = FeishuAdapter()

    message = await adapter.parse_incoming(feishu_event)

    assert message.message_type is MessageType.IMAGE
    assert message.content == "image post\ncaption"
    assert len(message.attachments) == 1
    assert message.attachments[0].file_key == "post_image_key"


async def test_feishu_parse_incoming_post_event_without_image_as_text(
    feishu_event: dict,
) -> None:
    feishu_event["event"]["message"]["message_type"] = "post"
    feishu_event["event"]["message"]["content"] = json.dumps(
        {
            "content": [
                [
                    {"tag": "text", "text": "hello "},
                    {"tag": "a", "text": "link", "href": "https://example.test"},
                ]
            ],
        }
    )
    adapter = FeishuAdapter()

    message = await adapter.parse_incoming(feishu_event)

    assert message.message_type is MessageType.TEXT
    assert message.content == "hello \nlink"
    assert message.attachments == []


async def test_feishu_send_message_gets_token_and_sends_post_payload(
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
        content="# Title\n\n- **item**\n\n```python\nprint('ok')\n```",
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
    assert_post_payload(
        send_kwargs["json"],
        receive_id="oc_chat_1",
        text="# Title\n\n- **item**\n\n```python\nprint('ok')\n```",
    )


async def test_feishu_send_message_sends_plain_text_as_post(
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
        content="plain reply",
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert len(http_client.calls) == 2
    assert_post_payload(
        http_client.calls[1][1]["json"],
        receive_id="oc_chat_1",
        text="plain reply",
    )


async def test_feishu_send_message_falls_back_to_text_when_post_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient(send_responses=[{"code": 999}, {"code": 0}])
    adapter = FeishuAdapter(http_client=http_client)
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="oc_chat_1",
        user_id="ou_user_1",
        content="**fallback**",
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert len(http_client.calls) == 3
    assert_post_payload(
        http_client.calls[1][1]["json"],
        receive_id="oc_chat_1",
        text="**fallback**",
    )
    assert http_client.calls[2][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "**fallback**"}, ensure_ascii=False),
    }


async def test_feishu_send_message_falls_back_to_text_when_post_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    request = httpx.Request("POST", "https://open.feishu.cn/open-apis/im/v1/messages")
    response = httpx.Response(400, request=request)
    http_client = FakeHTTPClient(
        send_responses=[
            httpx.HTTPStatusError("bad request", request=request, response=response),
            {"code": 0},
        ]
    )
    adapter = FeishuAdapter(http_client=http_client)
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="oc_chat_1",
        user_id="ou_user_1",
        content="**fallback**",
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert len(http_client.calls) == 3
    assert http_client.calls[2][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "**fallback**"}, ensure_ascii=False),
    }


async def test_feishu_send_message_uses_text_when_post_payload_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEISHU_APP_ID", "app-id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "app-secret")
    http_client = FakeHTTPClient()
    adapter = FeishuAdapter(http_client=http_client)
    long_text = "a" * (30 * 1024)
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="oc_chat_1",
        user_id="ou_user_1",
        content=long_text,
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert len(http_client.calls) == 2
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": long_text}, ensure_ascii=False),
    }


async def test_feishu_send_message_uses_text_for_blank_reply(
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
        content="  \n",
    )

    sent = await adapter.send_message(message)

    assert sent is True
    assert http_client.calls[1][1]["json"] == {
        "receive_id": "oc_chat_1",
        "msg_type": "text",
        "content": json.dumps({"text": "  \n"}, ensure_ascii=False),
    }
