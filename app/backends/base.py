from abc import ABC, abstractmethod

from app.core.models import UnifiedMessage


class LLMBackend(ABC):
    @abstractmethod
    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError
