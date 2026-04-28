from app.core.dedup import DeduplicationStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None, bool]] = []

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return False

        self.values[key] = value
        return True


async def test_deduplication_store_returns_true_for_first_message() -> None:
    redis = FakeRedis()
    store = DeduplicationStore(redis_client=redis)

    assert await store.mark_seen("om_message_1") is True
    assert redis.values == {"dedup:om_message_1": "1"}


async def test_deduplication_store_returns_false_for_duplicate_message() -> None:
    redis = FakeRedis()
    store = DeduplicationStore(redis_client=redis)

    assert await store.mark_seen("om_message_1") is True
    assert await store.mark_seen("om_message_1") is False
    assert redis.set_calls == [
        ("dedup:om_message_1", "1", 86400, True),
        ("dedup:om_message_1", "1", 86400, True),
    ]


async def test_deduplication_store_allows_different_message_ids() -> None:
    redis = FakeRedis()
    store = DeduplicationStore(redis_client=redis)

    assert await store.mark_seen("om_message_1") is True
    assert await store.mark_seen("om_message_2") is True
    assert redis.values == {
        "dedup:om_message_1": "1",
        "dedup:om_message_2": "1",
    }


async def test_deduplication_store_uses_ttl_and_prefix() -> None:
    redis = FakeRedis()
    store = DeduplicationStore(
        redis_client=redis,
        ttl_seconds=60,
        key_prefix="feishu-dedup",
    )

    assert await store.mark_seen("om_message_1") is True
    assert redis.set_calls == [("feishu-dedup:om_message_1", "1", 60, True)]
