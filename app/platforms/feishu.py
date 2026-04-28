import json
import os
import secrets
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.core.models import Attachment, MessageType, PlatformType, UnifiedMessage
from app.platforms.base import PlatformAdapter


class FeishuAdapter(PlatformAdapter):
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://open.feishu.cn",
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")

    async def parse_incoming(self, raw: dict) -> UnifiedMessage:
        event_type = raw.get("header", {}).get("event_type")
        event = raw.get("event", {})
        message = event.get("message", {})
        if event_type != "im.message.receive_v1":
            raise ValueError(f"unsupported feishu event type: {event_type}")
        message_type = message.get("message_type")
        if message_type not in {"text", "image", "file", "post"}:
            raise ValueError(
                f"unsupported feishu message type: {message.get('message_type')}"
            )

        content = json.loads(message.get("content") or "{}")
        sender_id = event.get("sender", {}).get("sender_id", {})
        attachments: list[Attachment] = []
        text = ""
        if message_type == "text":
            parsed_message_type = MessageType.TEXT
            text = content.get("text", "")
        elif message_type == "image":
            parsed_message_type = MessageType.IMAGE
            image_key = content.get("image_key")
            attachments.append(Attachment(file_key=image_key))
        elif message_type == "file":
            parsed_message_type = MessageType.FILE
            attachments.append(
                Attachment(
                    file_key=content.get("file_key"),
                    file_name=content.get("file_name"),
                    mime_type=content.get("mime_type"),
                    size=content.get("size"),
                )
            )
        else:
            text, attachments = self._parse_post_content(content)
            parsed_message_type = MessageType.IMAGE if attachments else MessageType.TEXT

        return UnifiedMessage(
            platform=PlatformType.FEISHU,
            message_type=parsed_message_type,
            session_id=message["chat_id"],
            user_id=sender_id["open_id"],
            content=text,
            message_id=message.get("message_id"),
            attachments=attachments,
            raw=raw,
        )

    async def verify_signature(self, request: Request) -> bool:
        expected_token = os.getenv("FEISHU_VERIFICATION_TOKEN")
        if not expected_token:
            return False

        payload = await self._request_json(request)
        token = payload.get("token")
        if not isinstance(token, str):
            token = payload.get("header", {}).get("token")
        if not isinstance(token, str):
            return False
        return secrets.compare_digest(token, expected_token)

    async def send_message(self, msg: UnifiedMessage) -> bool:
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

        if self._http_client is not None:
            return await self._send_message_with_client(
                self._http_client,
                msg,
                app_id,
                app_secret,
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            return await self._send_message_with_client(client, msg, app_id, app_secret)

    async def handle_challenge(self, request: Request) -> Response | None:
        payload = await self._request_json(request)
        if payload.get("type") != "url_verification":
            return None

        if not await self.verify_signature(request):
            return JSONResponse({"detail": "invalid verification token"}, status_code=401)
        return JSONResponse({"challenge": payload.get("challenge")})

    async def _send_message_with_client(
        self,
        client: Any,
        msg: UnifiedMessage,
        app_id: str,
        app_secret: str,
    ) -> bool:
        token_response = await client.post(
            f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        token_response.raise_for_status()
        token_payload = token_response.json()
        if token_payload.get("code", 0) != 0:
            return False

        tenant_token = token_payload.get("tenant_access_token")
        if not tenant_token:
            return False

        send_response = await client.post(
            f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {tenant_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": msg.session_id,
                "msg_type": "text",
                "content": json.dumps({"text": msg.content}, ensure_ascii=False),
            },
        )
        send_response.raise_for_status()
        return send_response.json().get("code", 0) == 0

    async def _request_json(self, request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            return {}
        return payload

    def _parse_post_content(self, content: dict[str, Any]) -> tuple[str, list[Attachment]]:
        text_parts: list[str] = []
        attachments: list[Attachment] = []
        title = content.get("title")
        if isinstance(title, str) and title:
            text_parts.append(title)

        for line in content.get("content") or []:
            if not isinstance(line, list):
                continue
            for item in line:
                if not isinstance(item, dict):
                    continue
                tag = item.get("tag")
                if tag == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif tag == "a" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif tag == "at" and isinstance(item.get("user_name"), str):
                    text_parts.append(item["user_name"])
                elif tag == "img" and isinstance(item.get("image_key"), str):
                    attachments.append(Attachment(file_key=item["image_key"]))

        return "\n".join(part for part in text_parts if part), attachments
