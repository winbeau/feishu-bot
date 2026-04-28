import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.models import Attachment

logger = logging.getLogger(__name__)


class FeishuFileDownloadError(Exception):
    pass


class FeishuFilePermissionError(FeishuFileDownloadError):
    pass


class FeishuFileNotFoundError(FeishuFileDownloadError):
    pass


class FeishuFileService:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        base_url: str = "https://open.feishu.cn",
        download_dir: str | Path | None = None,
        timeout_seconds: float | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self._http_client = http_client
        self._base_url = base_url.rstrip("/")
        self._download_dir = Path(
            download_dir
            or os.getenv("FEISHU_FILE_DOWNLOAD_DIR")
            or "/tmp/feishu-bot-files"
        )
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("FEISHU_FILE_DOWNLOAD_TIMEOUT_SECONDS", "30")
        )
        self._max_bytes = int(
            max_bytes
            if max_bytes is not None
            else os.getenv("FEISHU_FILE_MAX_BYTES", "104857600")
        )

    async def download_attachment(
        self,
        message_id: str,
        attachment: Attachment,
        file_type: str,
    ) -> Attachment:
        if not attachment.file_key:
            raise FeishuFileDownloadError("attachment is missing file_key")

        if self._http_client is not None:
            return await self._download_with_client(
                self._http_client,
                message_id,
                attachment,
                file_type,
            )

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await self._download_with_client(
                client,
                message_id,
                attachment,
                file_type,
            )

    async def _download_with_client(
        self,
        client: Any,
        message_id: str,
        attachment: Attachment,
        file_type: str,
    ) -> Attachment:
        token = await self._get_tenant_access_token(client)
        url = (
            f"{self._base_url}/open-apis/im/v1/messages/{message_id}"
            f"/resources/{attachment.file_key}?type={file_type}"
        )
        try:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "feishu file download network error",
                extra={
                    "event": "feishu_file_download",
                    "message_id": message_id,
                    "file_key": attachment.file_key,
                    "file_type": file_type,
                },
            )
            raise FeishuFileDownloadError("failed to download feishu file") from exc

        self._raise_for_download_response(response, message_id, attachment, file_type)
        content = response.content
        if len(content) > self._max_bytes:
            raise FeishuFileDownloadError("feishu file exceeds max bytes")

        self._download_dir.mkdir(parents=True, exist_ok=True)
        path = self._download_dir / self._safe_file_name(attachment)
        path.write_bytes(content)
        attachment.local_path = str(path)
        response_mime_type = self._response_mime_type(response)
        if response_mime_type:
            attachment.mime_type = response_mime_type
        if attachment.url and not self._is_public_http_url(attachment.url):
            attachment.url = None
        logger.info(
            "feishu file downloaded",
            extra={
                "event": "feishu_file_download",
                "message_id": message_id,
                "file_key": attachment.file_key,
                "file_type": file_type,
                "status_code": getattr(response, "status_code", None),
            },
        )
        return attachment

    async def _get_tenant_access_token(self, client: Any) -> str:
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            raise FeishuFileDownloadError(
                "FEISHU_APP_ID and FEISHU_APP_SECRET are required"
            )

        try:
            response = await client.post(
                f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise FeishuFileDownloadError("failed to get feishu tenant token") from exc

        if payload.get("code", 0) != 0 or not payload.get("tenant_access_token"):
            raise FeishuFileDownloadError("failed to get feishu tenant token")
        return str(payload["tenant_access_token"])

    def _raise_for_download_response(
        self,
        response: Any,
        message_id: str,
        attachment: Attachment,
        file_type: str,
    ) -> None:
        status_code = getattr(response, "status_code", 200)
        log_extra = {
            "event": "feishu_file_download",
            "message_id": message_id,
            "file_key": attachment.file_key,
            "file_type": file_type,
            "status_code": status_code,
        }
        if status_code in {401, 403}:
            logger.warning("feishu file permission denied", extra=log_extra)
            raise FeishuFilePermissionError("feishu file permission denied")
        if status_code == 404:
            logger.warning("feishu file not found", extra=log_extra)
            raise FeishuFileNotFoundError("feishu file not found")
        if status_code >= 400:
            logger.warning("feishu file download failed", extra=log_extra)
            raise FeishuFileDownloadError("feishu file download failed")

        content_type = ""
        headers = getattr(response, "headers", {}) or {}
        if hasattr(headers, "get"):
            content_type = headers.get("content-type", "")
        if "json" in content_type.lower():
            try:
                payload = response.json()
            except ValueError:
                return
            if payload.get("code", 0) != 0:
                message = str(payload.get("msg") or payload.get("message") or "")
                if "not" in message.lower():
                    raise FeishuFileNotFoundError("feishu file not found")
                raise FeishuFileDownloadError("feishu file download failed")

    def _safe_file_name(self, attachment: Attachment) -> str:
        source = attachment.file_name or attachment.file_key or "feishu-file"
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", source).strip("._")
        if not cleaned:
            cleaned = "feishu-file"
        if attachment.file_key and attachment.file_key not in cleaned:
            cleaned = f"{attachment.file_key}_{cleaned}"
        return cleaned

    def _is_public_http_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _response_mime_type(self, response: Any) -> str | None:
        headers = getattr(response, "headers", {}) or {}
        if not hasattr(headers, "get"):
            return None
        content_type = headers.get("content-type") or headers.get("Content-Type")
        if not isinstance(content_type, str):
            return None
        return content_type.split(";", 1)[0].strip().lower() or None
