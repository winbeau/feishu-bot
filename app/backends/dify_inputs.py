import json
from typing import Any
from urllib.parse import urlparse

from app.core.models import Attachment, MessageType, PlatformType, UnifiedMessage


class DifyInputBuilder:
    def build_payload(
        self,
        message: UnifiedMessage,
        session_id: str,
        response_mode: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "inputs": self.build_inputs(message, session_id),
            "query": message.content,
            "response_mode": response_mode,
            "conversation_id": "",
            "user": message.user_id,
        }
        files = self.build_files(message)
        if files:
            payload["files"] = files
        return payload

    def build_inputs(
        self,
        message: UnifiedMessage,
        session_id: str,
    ) -> dict[str, str]:
        image_urls = [
            attachment.url
            for attachment in message.attachments
            if message.message_type is MessageType.IMAGE and self._is_public_http_url(attachment.url)
        ]
        parsed_text = "\n\n".join(
            attachment.parsed_text or ""
            for attachment in message.attachments
            if attachment.parsed_text
        )
        file_tags = [
            tag
            for attachment in message.attachments
            for tag in attachment.file_tags
        ]
        return {
            "feishu_user_id": message.user_id
            if message.platform is PlatformType.FEISHU
            else "",
            "session_id": session_id,
            "message_type": message.message_type.value,
            "file_list": json.dumps(
                [self._attachment_metadata(attachment) for attachment in message.attachments],
                ensure_ascii=False,
            ),
            "image_urls": json.dumps(image_urls, ensure_ascii=False),
            "parsed_text": parsed_text,
            "file_tags": json.dumps(file_tags, ensure_ascii=False),
            "conversation_summary": message.conversation_summary or "",
        }

    def build_files(self, message: UnifiedMessage) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        for attachment in message.attachments:
            if attachment.dify_upload_file_id and attachment.dify_file_type:
                files.append(
                    {
                        "type": attachment.dify_file_type,
                        "transfer_method": "local_file",
                        "upload_file_id": attachment.dify_upload_file_id,
                    }
                )
                continue

            if (
                message.message_type is MessageType.IMAGE
                and self._is_public_http_url(attachment.url)
            ):
                files.append(
                    {
                        "type": "image",
                        "transfer_method": "remote_url",
                        "url": attachment.url or "",
                    }
                )
        return files

    def _attachment_metadata(self, attachment: Attachment) -> dict[str, Any]:
        return {
            "file_key": attachment.file_key,
            "file_name": attachment.file_name,
            "mime_type": attachment.mime_type,
            "size": attachment.size,
            "url": attachment.url,
            "dify_upload_file_id": attachment.dify_upload_file_id,
            "dify_file_type": attachment.dify_file_type,
            "file_tags": attachment.file_tags,
        }

    def _is_public_http_url(self, url: str | None) -> bool:
        if not url:
            return False
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
