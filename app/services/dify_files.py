import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.core.models import Attachment

logger = logging.getLogger(__name__)


class DifyFileUploadError(Exception):
    pass


class DifyFilePermissionError(DifyFileUploadError):
    pass


class DifyFileTooLargeError(DifyFileUploadError):
    pass


class DifyFileUnsupportedError(DifyFileUploadError):
    pass


class DifyFileUploadService:
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self._http_client = http_client
        self._base_url = (
            base_url or os.getenv("DIFY_BASE_URL") or "https://api.dify.ai/v1"
        ).rstrip("/")
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("DIFY_FILE_UPLOAD_TIMEOUT_SECONDS", "30")
        )
        self._max_bytes = int(
            max_bytes
            if max_bytes is not None
            else os.getenv("DIFY_FILE_UPLOAD_MAX_BYTES", "15728640")
        )

    async def upload_attachment(
        self,
        attachment: Attachment,
        user_id: str,
        dify_file_type: str,
    ) -> Attachment:
        if not attachment.local_path:
            raise DifyFileUploadError("attachment is missing local_path")

        path = Path(attachment.local_path)
        if not path.is_file():
            raise DifyFileUploadError("attachment local_path does not exist")
        if path.stat().st_size > self._max_bytes:
            self._log_upload_result(
                "dify file too large",
                attachment,
                status_code=413,
            )
            raise DifyFileTooLargeError("dify file exceeds max bytes")

        api_key = os.getenv("DIFY_API_KEY")
        if not api_key:
            raise DifyFileUploadError("DIFY_API_KEY is required")

        if self._http_client is not None:
            return await self._upload_with_client(
                self._http_client,
                attachment,
                path,
                user_id,
                dify_file_type,
                api_key,
            )

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await self._upload_with_client(
                client,
                attachment,
                path,
                user_id,
                dify_file_type,
                api_key,
            )

    async def _upload_with_client(
        self,
        client: Any,
        attachment: Attachment,
        path: Path,
        user_id: str,
        dify_file_type: str,
        api_key: str,
    ) -> Attachment:
        try:
            with path.open("rb") as handle:
                response = await client.post(
                    f"{self._base_url}/files/upload",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"user": user_id},
                    files={
                        "file": (
                            attachment.file_name or path.name,
                            handle,
                            attachment.mime_type or "application/octet-stream",
                        )
                    },
                    timeout=self._timeout_seconds,
                )
        except httpx.HTTPError as exc:
            self._log_upload_result("dify file upload network error", attachment)
            raise DifyFileUploadError("failed to upload dify file") from exc

        self._raise_for_upload_response(response, attachment)

        try:
            payload = response.json()
        except ValueError as exc:
            raise DifyFileUploadError("Dify file upload returned invalid JSON") from exc

        file_id = payload.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise DifyFileUploadError("Dify file upload response is missing id")

        attachment.dify_upload_file_id = file_id
        attachment.dify_file_type = dify_file_type
        self._log_upload_result(
            "dify file uploaded",
            attachment,
            status_code=getattr(response, "status_code", None),
        )
        return attachment

    def _raise_for_upload_response(self, response: Any, attachment: Attachment) -> None:
        status_code = getattr(response, "status_code", 200)
        if status_code < 400:
            return

        self._log_upload_result(
            "dify file upload failed",
            attachment,
            status_code=status_code,
        )
        if status_code in {401, 403}:
            raise DifyFilePermissionError("dify file upload permission denied")
        if status_code == 413:
            raise DifyFileTooLargeError("dify file exceeds max bytes")
        if status_code == 415:
            raise DifyFileUnsupportedError("dify file type is unsupported")
        raise DifyFileUploadError("dify file upload failed")

    def _log_upload_result(
        self,
        message: str,
        attachment: Attachment,
        *,
        status_code: int | None = None,
    ) -> None:
        logger.info(
            message,
            extra={
                "event": "dify_file_upload",
                "file_name": attachment.file_name,
                "mime_type": attachment.mime_type,
                "status_code": status_code,
            },
        )
