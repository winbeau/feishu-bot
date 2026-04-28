import json

import httpx
import pytest

from app.backends.dify import BackendError, DifyBackend
from app.core.models import Attachment, MessageType, PlatformType, UnifiedMessage


class FakeResponse:
    def __init__(
        self,
        payload: dict | None = None,
        *,
        status_code: int = 200,
        lines: list[str] | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self._lines = lines or []

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://dify.example.test/chat-messages")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code} response",
                request=request,
                response=response,
            )

    def json(self) -> dict:
        return self._payload

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStream:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeHTTPClient:
    def __init__(
        self,
        *,
        post_results: list[FakeResponse | BaseException] | None = None,
        stream_response: FakeResponse | None = None,
        get_result: FakeResponse | BaseException | None = None,
    ) -> None:
        self.post_results = post_results or []
        self.stream_response = stream_response
        self.get_result = get_result
        self.post_calls: list[tuple[str, dict]] = []
        self.stream_calls: list[tuple[str, str, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.post_calls.append((url, kwargs))
        result = self.post_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def stream(self, method: str, url: str, **kwargs) -> FakeStream:
        self.stream_calls.append((method, url, kwargs))
        assert self.stream_response is not None
        return FakeStream(self.stream_response)

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.get_calls.append((url, kwargs))
        assert self.get_result is not None
        if isinstance(self.get_result, BaseException):
            raise self.get_result
        return self.get_result


@pytest.fixture
def dify_message() -> UnifiedMessage:
    return UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.TEXT,
        session_id="chat-session-1",
        user_id="user-1",
        content="hello dify",
        message_id="message-1",
    )


async def test_dify_blocking_chat_posts_expected_payload_and_returns_answer(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(
        post_results=[FakeResponse({"answer": "hello from dify"})]
    )
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )

    answer = await backend.chat(dify_message, session_id="conversation-1")

    assert answer == "hello from dify"
    assert len(http_client.post_calls) == 1
    url, kwargs = http_client.post_calls[0]
    assert url == "https://dify.example.test/chat-messages"
    assert kwargs["headers"] == {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
    }
    assert kwargs["json"] == {
        "inputs": {
            "feishu_user_id": "user-1",
            "session_id": "conversation-1",
            "message_type": "text",
            "file_list": "[]",
            "image_urls": "[]",
            "parsed_text": "",
            "file_tags": "[]",
            "conversation_summary": "",
        },
        "query": "hello dify",
        "response_mode": "blocking",
        "conversation_id": "",
        "user": "user-1",
    }


async def test_dify_streaming_chat_aggregates_message_events(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    lines = [
        'data: {"event": "message", "answer": "hello "}',
        'data: {"event": "message", "answer": "stream"}',
        'data: {"event": "message_end"}',
        "data: [DONE]",
    ]
    http_client = FakeHTTPClient(stream_response=FakeResponse(lines=lines))
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="streaming",
    )

    answer = await backend.chat(dify_message, session_id="conversation-1")

    assert answer == "hello stream"
    assert len(http_client.stream_calls) == 1
    method, url, kwargs = http_client.stream_calls[0]
    assert method == "POST"
    assert url == "https://dify.example.test/chat-messages"
    assert kwargs["json"]["response_mode"] == "streaming"


async def test_dify_timeout_retries_twice_then_returns_answer(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(
        post_results=[
            httpx.TimeoutException("first timeout"),
            httpx.TimeoutException("second timeout"),
            FakeResponse({"answer": "after retry"}),
        ]
    )
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )

    answer = await backend.chat(dify_message, session_id="conversation-1")

    assert answer == "after retry"
    assert len(http_client.post_calls) == 3


async def test_dify_4xx_error_raises_backend_error_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(post_results=[FakeResponse(status_code=400)])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )

    with pytest.raises(BackendError):
        await backend.chat(dify_message, session_id="conversation-1")

    assert len(http_client.post_calls) == 1


async def test_dify_chat_raises_backend_error_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.delenv("DIFY_API_KEY", raising=False)
    http_client = FakeHTTPClient(
        post_results=[FakeResponse({"answer": "should not call"})]
    )
    backend = DifyBackend(http_client=http_client, base_url="https://dify.example.test")

    with pytest.raises(BackendError):
        await backend.chat(dify_message, session_id="conversation-1")

    assert http_client.post_calls == []


async def test_dify_health_check_returns_true_for_parameters_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(get_result=FakeResponse({"opening_statement": ""}))
    backend = DifyBackend(http_client=http_client, base_url="https://dify.example.test")

    healthy = await backend.health_check()

    assert healthy is True
    assert http_client.get_calls == [
        (
            "https://dify.example.test/parameters",
            {
                "headers": {
                    "Authorization": "Bearer test-key",
                    "Content-Type": "application/json",
                }
            },
        )
    ]


async def test_dify_health_check_returns_false_for_http_or_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(get_result=httpx.ConnectError("network unavailable"))
    backend = DifyBackend(http_client=http_client, base_url="https://dify.example.test")

    assert await backend.health_check() is False


async def test_dify_streaming_error_event_raises_backend_error(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(
        stream_response=FakeResponse(
            lines=[
                "data: "
                + json.dumps(
                    {
                        "event": "error",
                        "message": "invalid request",
                    }
                )
            ]
        )
    )
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="streaming",
    )

    with pytest.raises(BackendError):
        await backend.chat(dify_message, session_id="conversation-1")


async def test_dify_defaults_to_streaming_response_mode(
    monkeypatch: pytest.MonkeyPatch,
    dify_message: UnifiedMessage,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(
        stream_response=FakeResponse(
            lines=['data: {"event": "message", "answer": "default stream"}']
        )
    )
    backend = DifyBackend(http_client=http_client, base_url="https://dify.example.test")

    answer = await backend.chat(dify_message, session_id="conversation-1")

    assert answer == "default stream"
    assert http_client.stream_calls[0][2]["json"]["response_mode"] == "streaming"


async def test_dify_payload_includes_attachment_inputs_and_remote_image_files(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    caplog.set_level("INFO")
    http_client = FakeHTTPClient(
        post_results=[FakeResponse({"answer": "attachment answer"})]
    )
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.IMAGE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[
            Attachment(
                file_key="img-key",
                url="https://cdn.example.test/image.png",
                parsed_text="ignored for image",
                file_tags=["downloaded"],
            ),
            Attachment(file_key="local-only", local_path="/tmp/local.png"),
        ],
        conversation_summary="previous turn",
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert payload["inputs"]["session_id"] == "session-1"
    assert payload["inputs"]["image_urls"] == '["https://cdn.example.test/image.png"]'
    assert payload["inputs"]["parsed_text"] == "ignored for image"
    assert payload["inputs"]["file_tags"] == '["downloaded"]'
    assert payload["inputs"]["conversation_summary"] == "previous turn"
    assert payload["files"] == [
        {
            "type": "image",
            "transfer_method": "remote_url",
            "url": "https://cdn.example.test/image.png",
        }
    ]
    assert "dify payload includes files session_id=session-1 file_count=1" in caplog.text
    assert '"transfer_method": "remote_url"' in caplog.text
    assert "https://cdn.example.test/image.png" in caplog.text
    assert 'image_urls=["https://cdn.example.test/image.png"]' in caplog.text
    assert "query=请分析这张图片" in caplog.text


async def test_dify_payload_includes_uploaded_image_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(post_results=[FakeResponse({"answer": "ok"})])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.IMAGE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[
            Attachment(
                local_path="/tmp/downloaded.png",
                dify_upload_file_id="upload-id-1",
                dify_file_type="image",
            )
        ],
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert payload["query"] == "请分析这张图片"
    assert payload["files"] == [
        {
            "type": "image",
            "transfer_method": "local_file",
            "upload_file_id": "upload-id-1",
        }
    ]


async def test_dify_payload_prefers_public_url_over_uploaded_image_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(post_results=[FakeResponse({"answer": "ok"})])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.IMAGE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[
            Attachment(
                url="https://bot.example.test/public/files/image.png",
                dify_upload_file_id="upload-id-1",
                dify_file_type="image",
            )
        ],
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert payload["inputs"]["image_urls"] == (
        '["https://bot.example.test/public/files/image.png"]'
    )
    assert payload["query"] == "请分析这张图片"
    assert payload["files"] == [
        {
            "type": "image",
            "transfer_method": "remote_url",
            "url": "https://bot.example.test/public/files/image.png",
        }
    ]


async def test_dify_payload_uses_configured_default_query_for_empty_image_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    monkeypatch.setenv("DIFY_IMAGE_DEFAULT_QUERY", "这张图什么意思")
    http_client = FakeHTTPClient(post_results=[FakeResponse({"answer": "ok"})])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.IMAGE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[
            Attachment(
                dify_upload_file_id="upload-id-1",
                dify_file_type="image",
            )
        ],
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert payload["query"] == "这张图什么意思"


async def test_dify_payload_skips_local_file_without_upload_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(post_results=[FakeResponse({"answer": "ok"})])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.IMAGE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[Attachment(local_path="/tmp/downloaded.png")],
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert "files" not in payload


async def test_dify_payload_supports_uploaded_document_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIFY_API_KEY", "test-key")
    http_client = FakeHTTPClient(post_results=[FakeResponse({"answer": "ok"})])
    backend = DifyBackend(
        http_client=http_client,
        base_url="https://dify.example.test",
        response_mode="blocking",
    )
    message = UnifiedMessage(
        platform=PlatformType.FEISHU,
        message_type=MessageType.FILE,
        session_id="session-1",
        user_id="user-1",
        content="",
        attachments=[
            Attachment(
                local_path="/tmp/report.pdf",
                dify_upload_file_id="upload-doc-1",
                dify_file_type="document",
            )
        ],
    )

    await backend.chat(message, session_id="session-1")

    payload = http_client.post_calls[0][1]["json"]
    assert payload["files"] == [
        {
            "type": "document",
            "transfer_method": "local_file",
            "upload_file_id": "upload-doc-1",
        }
    ]
