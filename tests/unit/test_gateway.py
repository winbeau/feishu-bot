import pytest

from app.core.gateway import Gateway
from app.core.models import MessageType, PlatformType, UnifiedMessage


class RecordingBackend:
    def __init__(self, reply: str = "backend reply") -> None:
        self.reply = reply
        self.calls: list[tuple[UnifiedMessage, str]] = []

    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        self.calls.append((message, session_id))
        return self.reply


class RaisingBackend:
    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        raise RuntimeError("backend unavailable")


@pytest.fixture
def text_message() -> UnifiedMessage:
    return UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="session-1",
        user_id="user-1",
        content="hello",
        message_id="message-1",
    )


async def test_gateway_routes_message_to_backend_and_returns_reply(
    text_message: UnifiedMessage,
) -> None:
    backend = RecordingBackend(reply="hello from backend")
    gateway = Gateway(backend)

    reply = await gateway.route(text_message)

    assert reply == "hello from backend"
    assert backend.calls == [(text_message, "session-1")]


async def test_gateway_returns_fallback_reply_when_backend_raises(
    text_message: UnifiedMessage,
) -> None:
    gateway = Gateway(RaisingBackend())

    reply = await gateway.route(text_message)

    assert reply == "抱歉，服务暂时不可用，请稍后再试。"
