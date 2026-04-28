from typing import Protocol

from app.core.models import UnifiedMessage


FALLBACK_REPLY = "抱歉，服务暂时不可用，请稍后再试。"


class ChatBackend(Protocol):
    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        ...


class Gateway:
    def __init__(self, backend: ChatBackend) -> None:
        self._backend = backend

    async def route(self, message: UnifiedMessage) -> str:
        try:
            return await self._backend.chat(message, message.session_id)
        except Exception:
            return FALLBACK_REPLY
