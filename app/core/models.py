from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"


class PlatformType(str, Enum):
    FEISHU = "feishu"
    WECHAT = "wechat"
    QQ = "qq"


class Attachment(BaseModel):
    file_key: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    size: int | None = None
    url: str | None = None
    local_path: str | None = None
    parsed_text: str | None = None
    dify_upload_file_id: str | None = None
    dify_file_type: str | None = None
    file_tags: list[str] = Field(default_factory=list)


class UnifiedMessage(BaseModel):
    platform: PlatformType
    message_type: MessageType
    session_id: str
    user_id: str
    content: str
    message_id: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    conversation_summary: str = ""
    raw: dict[str, Any] | None = None
