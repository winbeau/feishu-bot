from enum import Enum
from typing import Any

from pydantic import BaseModel


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"


class PlatformType(str, Enum):
    FEISHU = "feishu"
    WECHAT = "wechat"
    QQ = "qq"


class UnifiedMessage(BaseModel):
    platform: PlatformType
    message_type: MessageType
    session_id: str
    user_id: str
    content: str
    message_id: str | None = None
    raw: dict[str, Any] | None = None
