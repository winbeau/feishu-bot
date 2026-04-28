import os
import uuid
from collections.abc import Callable
from enum import Enum

from redis import asyncio as redis

from app.core.models import PlatformType


class SessionStore:
    def __init__(
        self,
        redis_client=None,
        ttl_seconds: int = 3600,
        id_factory: Callable[[], str] | None = None,
        key_prefix: str = "session",
    ) -> None:
        if redis_client is None:
            redis_client = redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6380/0")
            )
        self.redis_client = redis_client
        self.ttl_seconds = ttl_seconds
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self.key_prefix = key_prefix

    async def get_session_id(self, key: str) -> str | None:
        value = await self.redis_client.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode()
        return value

    async def set_session_id(self, key: str, session_id: str) -> None:
        await self.redis_client.set(key, session_id, ex=self.ttl_seconds)

    async def get_or_create_session_id(
        self, platform: PlatformType | str, user_id: str
    ) -> str:
        key = self._session_key(platform, user_id)
        session_id = await self.get_session_id(key)
        if session_id is not None:
            await self.redis_client.expire(key, self.ttl_seconds)
            return session_id

        session_id = self.id_factory()
        await self.set_session_id(key, session_id)
        return session_id

    def _session_key(self, platform: PlatformType | str, user_id: str) -> str:
        platform_value = platform.value if isinstance(platform, Enum) else platform
        return f"{self.key_prefix}:{platform_value}:{user_id}"


class ConversationSummaryStore:
    def __init__(
        self,
        redis_client=None,
        ttl_seconds: int | None = None,
        max_chars: int | None = None,
        key_prefix: str = "summary",
    ) -> None:
        if redis_client is None:
            redis_client = redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6380/0")
            )
        self.redis_client = redis_client
        self.ttl_seconds = int(
            ttl_seconds
            if ttl_seconds is not None
            else os.getenv("SUMMARY_TTL_SECONDS", "604800")
        )
        self.max_chars = int(
            max_chars
            if max_chars is not None
            else os.getenv("SUMMARY_MAX_CHARS", "2000")
        )
        self.key_prefix = key_prefix

    async def get_summary(self, platform: PlatformType | str, user_id: str) -> str:
        value = await self.redis_client.get(self._summary_key(platform, user_id))
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode()
        return str(value)

    async def update_summary(
        self,
        platform: PlatformType | str,
        user_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> str:
        existing = await self.get_summary(platform, user_id)
        appended = (
            f"{existing}\n"
            if existing
            else ""
        ) + f"User: {user_message}\nAssistant: {assistant_reply}"
        summary = appended[-self.max_chars :]
        await self.redis_client.set(
            self._summary_key(platform, user_id),
            summary,
            ex=self.ttl_seconds,
        )
        return summary

    def _summary_key(self, platform: PlatformType | str, user_id: str) -> str:
        platform_value = platform.value if isinstance(platform, Enum) else platform
        return f"{self.key_prefix}:{platform_value}:{user_id}"
