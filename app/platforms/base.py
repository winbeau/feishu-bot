from abc import ABC, abstractmethod

from fastapi import Request, Response

from app.core.models import UnifiedMessage


class PlatformAdapter(ABC):
    @abstractmethod
    async def parse_incoming(self, raw: dict) -> UnifiedMessage:
        raise NotImplementedError

    @abstractmethod
    async def verify_signature(self, request: Request) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def send_message(self, msg: UnifiedMessage) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def handle_challenge(self, request: Request) -> Response | None:
        raise NotImplementedError
