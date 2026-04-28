from app.backends.base import LLMBackend
from app.core.models import UnifiedMessage


class DifyBackend(LLMBackend):
    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError
