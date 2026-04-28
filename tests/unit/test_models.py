import pytest
from pydantic import ValidationError

from app.core.models import MessageType, PlatformType, UnifiedMessage


def test_scaffold_imports_cleanly() -> None:
    import app.main
    import app.backends.base
    import app.backends.dify
    import app.core.dedup
    import app.core.gateway
    import app.core.models
    import app.core.session
    import app.platforms.base
    import app.platforms.feishu
    import app.platforms.qq
    import app.platforms.wechat

    assert app.main.app is not None


def test_unified_message_serializes_and_deserializes() -> None:
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="session-1",
        user_id="user-1",
        content="hello",
        message_id="message-1",
        raw={"event": {"message": {"message_id": "message-1"}}},
    )

    dumped = message.model_dump(mode="json")

    assert dumped == {
        "platform": "feishu",
        "message_type": "text",
        "session_id": "session-1",
        "user_id": "user-1",
        "content": "hello",
        "message_id": "message-1",
        "raw": {"event": {"message": {"message_id": "message-1"}}},
    }

    loaded = UnifiedMessage.model_validate(dumped)

    assert loaded.platform is PlatformType.FEISHU
    assert loaded.message_type is MessageType.TEXT
    assert loaded == message


@pytest.mark.parametrize("message_type", list(MessageType))
@pytest.mark.parametrize("platform", list(PlatformType))
def test_unified_message_supports_declared_message_and_platform_types(
    platform: PlatformType,
    message_type: MessageType,
) -> None:
    message = UnifiedMessage(
        platform=platform,
        message_type=message_type,
        session_id="session-1",
        user_id="user-1",
        content="payload",
    )

    assert message.platform is platform
    assert message.message_type is message_type


@pytest.mark.parametrize(
    "missing_field",
    ["platform", "session_id", "user_id", "message_type", "content"],
)
def test_unified_message_requires_core_fields(missing_field: str) -> None:
    data = {
        "platform": "feishu",
        "message_type": "text",
        "session_id": "session-1",
        "user_id": "user-1",
        "content": "hello",
    }
    data.pop(missing_field)

    with pytest.raises(ValidationError):
        UnifiedMessage.model_validate(data)
