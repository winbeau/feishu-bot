from typing import Protocol

from app.core.models import UnifiedMessage


FALLBACK_REPLY = "抱歉，服务暂时不可用，请稍后再试。"


class ChatBackend(Protocol):
    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        ...


class Gateway:
    def __init__(self, backend: ChatBackend, summary_store=None) -> None:
        self._backend = backend
        self._summary_store = summary_store

    async def route(self, message: UnifiedMessage) -> str:
        try:
            if self._summary_store is not None:
                message.conversation_summary = await self._summary_store.get_summary(
                    message.platform,
                    message.user_id,
                )
            reply = await self._backend.chat(message, message.session_id)
            if self._summary_store is not None:
                await self._summary_store.update_summary(
                    message.platform,
                    message.user_id,
                    message.content,
                    reply,
                )
            return reply
        except Exception:
            return FALLBACK_REPLY
