from app.core.models import UnifiedMessage


class Gateway:
    async def route(self, message: UnifiedMessage) -> str:
        raise NotImplementedError
