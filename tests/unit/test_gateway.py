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


class RecordingSummaryStore:
    def __init__(self, summary: str = "previous summary") -> None:
        self.summary = summary
        self.get_calls: list[tuple[PlatformType, str]] = []
        self.update_calls: list[tuple[PlatformType, str, str, str]] = []

    async def get_summary(self, platform, user_id: str) -> str:
        self.get_calls.append((platform, user_id))
        return self.summary

    async def update_summary(self, platform, user_id: str, user_message: str, reply: str):
        self.update_calls.append((platform, user_id, user_message, reply))
        return "updated summary"


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


async def test_gateway_reads_and_updates_conversation_summary(
    text_message: UnifiedMessage,
) -> None:
    backend = RecordingBackend(reply="summary reply")
    summary_store = RecordingSummaryStore()
    gateway = Gateway(backend, summary_store=summary_store)

    reply = await gateway.route(text_message)

    assert reply == "summary reply"
    assert text_message.conversation_summary == "previous summary"
    assert summary_store.get_calls == [(PlatformType.FEISHU, "user-1")]
    assert summary_store.update_calls == [
        (PlatformType.FEISHU, "user-1", "hello", "summary reply")
    ]


async def test_gateway_does_not_update_summary_when_backend_raises(
    text_message: UnifiedMessage,
) -> None:
    summary_store = RecordingSummaryStore()
    gateway = Gateway(RaisingBackend(), summary_store=summary_store)

    reply = await gateway.route(text_message)

    assert reply == "抱歉，服务暂时不可用，请稍后再试。"
    assert summary_store.update_calls == []
