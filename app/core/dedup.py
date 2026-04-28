import os
from typing import Any

import redis.asyncio as redis


class DeduplicationStore:
    def __init__(
        self,
        redis_client: Any | None = None,
        ttl_seconds: int = 86400,
        key_prefix: str = "dedup",
    ) -> None:
        self._redis = redis_client or redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6380/0")
        )
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    async def mark_seen(self, message_id: str) -> bool:
        result = await self._redis.set(
            f"{self._key_prefix}:{message_id}",
            "1",
            ex=self._ttl_seconds,
            nx=True,
        )
        return bool(result)
