import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.models import Attachment

logger = logging.getLogger(__name__)


class PublicFilePublishError(Exception):
    pass


class PublicFileService:
    _MIME_EXTENSIONS = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/gif": "gif",
        "image/webp": "webp",
    }

    def __init__(
        self,
        base_url: str | None = None,
        public_dir: str | Path | None = None,
    ) -> None:
        configured_base_url = (
            base_url if base_url is not None else os.getenv("PUBLIC_FILE_BASE_URL")
        )
        self._base_url = (configured_base_url or "").rstrip("/")
        self._public_dir = Path(
            public_dir
            or os.getenv("PUBLIC_FILE_DIR")
            or "/tmp/feishu-bot-public-files"
        )

    def publish_image(self, attachment: Attachment) -> Attachment:
        if not self._base_url:
            raise PublicFilePublishError("PUBLIC_FILE_BASE_URL is required")
        if not self._is_public_base_url(self._base_url):
            raise PublicFilePublishError("PUBLIC_FILE_BASE_URL must be an http(s) URL")
        if not attachment.local_path:
            raise PublicFilePublishError("attachment is missing local_path")

        source = Path(attachment.local_path)
        if not source.is_file():
            raise PublicFilePublishError("attachment local_path does not exist")

        content = source.read_bytes()
        extension = self._image_extension(content, attachment)
        if extension is None:
            raise PublicFilePublishError("unsupported or unrecognized image type")

        self._public_dir.mkdir(parents=True, exist_ok=True)
        public_name = f"{uuid.uuid4().hex}.{extension}"
        destination = self._public_dir / public_name
        shutil.copyfile(source, destination)
        attachment.url = f"{self._base_url}/public/files/{public_name}"
        logger.info(
            "public image published public_url=%s public_path=%s mime_type=%s",
            attachment.url,
            destination,
            attachment.mime_type,
            extra={
                "event": "public_image_publish",
                "public_url": attachment.url,
                "public_path": str(destination),
                "mime_type": attachment.mime_type,
            },
        )
        return attachment

    def _image_extension(
        self,
        content: bytes,
        attachment: Attachment,
    ) -> str | None:
        extension = self._extension_from_magic_bytes(content)
        if extension:
            return extension

        mime_type = self._normalize_mime_type(attachment.mime_type)
        if mime_type in self._MIME_EXTENSIONS:
            return self._MIME_EXTENSIONS[mime_type]

        return None

    def _extension_from_magic_bytes(self, content: bytes) -> str | None:
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if content.startswith(b"\xff\xd8\xff"):
            return "jpg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "gif"
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "webp"
        return None

    def _normalize_mime_type(self, mime_type: str | None) -> str:
        return (mime_type or "").split(";", 1)[0].strip().lower()

    def _is_public_base_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class PublicFileUrlValidator:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._http_client = http_client
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PUBLIC_FILE_VALIDATE_TIMEOUT_SECONDS", "5")
        )

    async def validate_image_url(self, url: str) -> None:
        if self._http_client is not None:
            await self._validate_with_client(self._http_client, url)
            return

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            await self._validate_with_client(client, url)

    async def _validate_with_client(self, client: Any, url: str) -> None:
        try:
            response = await client.head(
                url,
                follow_redirects=True,
                timeout=self._timeout_seconds,
            )
            if getattr(response, "status_code", 200) in {405, 501}:
                response = await client.get(
                    url,
                    headers={"Range": "bytes=0-0"},
                    follow_redirects=True,
                    timeout=self._timeout_seconds,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "public image url validation network error public_url=%s error=%s",
                url,
                exc,
                extra={"event": "public_image_url_validate", "public_url": url},
            )
            raise PublicFilePublishError("public image url is not reachable") from exc

        status_code = getattr(response, "status_code", 200)
        content_type = self._header(response, "content-type")
        content_length = self._header(response, "content-length")
        logger.info(
            "public image url validated public_url=%s status_code=%s "
            "content_type=%s content_length=%s",
            url,
            status_code,
            content_type,
            content_length,
            extra={
                "event": "public_image_url_validate",
                "public_url": url,
                "status_code": status_code,
                "content_type": content_type,
                "content_length": content_length,
            },
        )

        if status_code < 200 or status_code >= 300:
            raise PublicFilePublishError(
                f"public image url returned status {status_code}"
            )
        if not content_type.lower().startswith("image/"):
            raise PublicFilePublishError(
                f"public image url returned non-image content type: {content_type}"
            )

    def _header(self, response: Any, name: str) -> str:
        headers = getattr(response, "headers", {}) or {}
        if not hasattr(headers, "get"):
            return ""
        value = headers.get(name) or headers.get(name.title())
        return str(value or "")
