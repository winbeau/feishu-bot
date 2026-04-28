import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.backends.dify_inputs import DifyInputBuilder
from app.backends.base import LLMBackend
from app.core.models import UnifiedMessage

logger = logging.getLogger(__name__)


class BackendError(Exception):
    pass


class DifyBackend(LLMBackend):
    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        response_mode: str | None = None,
        max_retries: int = 2,
        input_builder: DifyInputBuilder | None = None,
    ) -> None:
        self._http_client = http_client
        self._base_url = (
            base_url or os.getenv("DIFY_BASE_URL") or "https://api.dify.ai/v1"
        ).rstrip("/")
        self._response_mode = response_mode or os.getenv("DIFY_RESPONSE_MODE") or "streaming"
        self._max_retries = max_retries
        self._input_builder = input_builder or DifyInputBuilder()

    async def chat(self, message: UnifiedMessage, session_id: str) -> str:
        if self._response_mode not in {"blocking", "streaming"}:
            raise BackendError(f"unsupported Dify response mode: {self._response_mode}")

        if self._http_client is not None:
            return await self._chat_with_client(self._http_client, message, session_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._chat_with_client(client, message, session_id)

    async def health_check(self) -> bool:
        if self._http_client is not None:
            return await self._health_check_with_client(self._http_client)

        async with httpx.AsyncClient(timeout=10.0) as client:
            return await self._health_check_with_client(client)

    async def _chat_with_client(
        self,
        client: Any,
        message: UnifiedMessage,
        session_id: str,
    ) -> str:
        async def do_request() -> str:
            if self._response_mode == "streaming":
                return await self._chat_streaming(client, message, session_id)
            return await self._chat_blocking(client, message, session_id)

        return await self._retry_timeouts(do_request)

    async def _chat_blocking(
        self,
        client: Any,
        message: UnifiedMessage,
        session_id: str,
    ) -> str:
        response = await client.post(
            f"{self._base_url}/chat-messages",
            headers=self._headers(),
            json=self._payload(message, session_id),
        )
        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendError("Dify returned invalid JSON") from exc

        answer = payload.get("answer")
        if not isinstance(answer, str):
            raise BackendError("Dify response is missing answer")
        return answer

    async def _chat_streaming(
        self,
        client: Any,
        message: UnifiedMessage,
        session_id: str,
    ) -> str:
        chunks: list[str] = []
        async with client.stream(
            "POST",
            f"{self._base_url}/chat-messages",
            headers=self._headers(),
            json=self._payload(message, session_id),
        ) as response:
            self._raise_for_status(response)
            async for line in response.aiter_lines():
                chunk = self._parse_sse_line(line)
                if chunk is not None:
                    chunks.append(chunk)
        return "".join(chunks)

    async def _health_check_with_client(self, client: Any) -> bool:
        headers = self._headers()
        try:
            response = await client.get(
                f"{self._base_url}/parameters",
                headers=headers,
            )
            self._raise_for_status(response)
        except BackendError:
            return False
        except httpx.HTTPError:
            return False
        return True

    async def _retry_timeouts(self, action: Callable[[], Awaitable[str]]) -> str:
        attempts = self._max_retries + 1
        last_timeout: httpx.TimeoutException | None = None
        for _ in range(attempts):
            try:
                return await action()
            except httpx.TimeoutException as exc:
                last_timeout = exc

        raise BackendError("Dify request timed out") from last_timeout

    def _headers(self) -> dict[str, str]:
        api_key = os.getenv("DIFY_API_KEY")
        if not api_key:
            raise BackendError("DIFY_API_KEY is required")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, message: UnifiedMessage, session_id: str) -> dict[str, Any]:
        payload = self._input_builder.build_payload(
            message,
            session_id,
            self._response_mode,
        )
        files = payload.get("files") or []
        if files:
            logger.info(
                "dify payload includes files",
                extra={
                    "event": "dify_payload_files",
                    "session_id": session_id,
                    "file_count": len(files),
                    "files": files,
                },
            )
        return payload

    def _raise_for_status(self, response: Any) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if 400 <= status_code < 500:
                raise BackendError(f"Dify request failed with {status_code}") from exc
            raise BackendError("Dify request failed") from exc

    def _parse_sse_line(self, line: str) -> str | None:
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None

        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            return None

        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise BackendError("Dify stream returned invalid JSON") from exc

        event_type = event.get("event")
        if event_type == "message":
            answer = event.get("answer", "")
            if not isinstance(answer, str):
                raise BackendError("Dify stream message is missing answer")
            return answer
        if event_type == "error":
            message = event.get("message") or event.get("error") or "Dify stream error"
            raise BackendError(str(message))
        return None
