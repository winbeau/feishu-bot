from fastapi import Request, Response

from app.core.models import UnifiedMessage
from app.platforms.base import PlatformAdapter


class FeishuAdapter(PlatformAdapter):
    async def parse_incoming(self, raw: dict) -> UnifiedMessage:
        raise NotImplementedError

    async def verify_signature(self, request: Request) -> bool:
        raise NotImplementedError

    async def send_message(self, msg: UnifiedMessage) -> bool:
        raise NotImplementedError

    async def handle_challenge(self, request: Request) -> Response | None:
        raise NotImplementedError
