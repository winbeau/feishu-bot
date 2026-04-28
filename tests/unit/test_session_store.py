from collections.abc import Callable

from app.core.models import PlatformType
from app.core.session import SessionStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.expires_at: dict[str, float] = {}
        self.now = 0.0
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.expire_calls: list[tuple[str, int]] = []

    async def get(self, key: str) -> str | bytes | None:
        if key in self.expires_at and self.expires_at[key] <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)
            return None
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))
        if ex is not None:
            self.expires_at[key] = self.now + ex

    async def expire(self, key: str, seconds: int) -> None:
        self.expire_calls.append((key, seconds))
        if key in self.values:
            self.expires_at[key] = self.now + seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def sequence_factory(*values: str) -> Callable[[], str]:
    remaining = list(values)

    def factory() -> str:
        return remaining.pop(0)

    return factory


async def test_session_store_creates_new_session_for_first_conversation() -> None:
    redis = FakeRedis()
    store = SessionStore(redis_client=redis, id_factory=lambda: "conversation-1")

    session_id = await store.get_or_create_session_id(PlatformType.FEISHU, "user-1")

    assert session_id == "conversation-1"
    assert redis.values == {"session:feishu:user-1": "conversation-1"}
    assert redis.set_calls == [("session:feishu:user-1", "conversation-1", 3600)]
    assert redis.expires_at["session:feishu:user-1"] == 3600


async def test_session_store_reuses_existing_session_for_same_user() -> None:
    redis = FakeRedis()
    await redis.set("session:feishu:user-1", "conversation-1", ex=3600)
    redis.set_calls.clear()
    generated: list[str] = []
    store = SessionStore(
        redis_client=redis,
        id_factory=lambda: generated.append("new-conversation") or "new-conversation",
    )

    session_id = await store.get_or_create_session_id("feishu", "user-1")

    assert session_id == "conversation-1"
    assert generated == []
    assert redis.set_calls == []
    assert redis.expire_calls == [("session:feishu:user-1", 3600)]


async def test_session_store_generates_new_session_after_ttl_expires() -> None:
    redis = FakeRedis()
    store = SessionStore(
        redis_client=redis,
        ttl_seconds=10,
        id_factory=sequence_factory("conversation-1", "conversation-2"),
    )

    first_session_id = await store.get_or_create_session_id(PlatformType.FEISHU, "user-1")
    redis.advance(11)
    second_session_id = await store.get_or_create_session_id(PlatformType.FEISHU, "user-1")

    assert first_session_id == "conversation-1"
    assert second_session_id == "conversation-2"
    assert redis.values == {"session:feishu:user-1": "conversation-2"}
    assert redis.set_calls == [
        ("session:feishu:user-1", "conversation-1", 10),
        ("session:feishu:user-1", "conversation-2", 10),
    ]


async def test_session_store_get_and_set_wrap_redis_values() -> None:
    redis = FakeRedis()
    store = SessionStore(redis_client=redis, ttl_seconds=120)

    assert await store.get_session_id("session:feishu:missing") is None

    redis.values["session:feishu:user-1"] = b"conversation-1"
    assert await store.get_session_id("session:feishu:user-1") == "conversation-1"

    await store.set_session_id("session:feishu:user-2", "conversation-2")

    assert redis.values["session:feishu:user-2"] == "conversation-2"
    assert redis.set_calls == [("session:feishu:user-2", "conversation-2", 120)]
