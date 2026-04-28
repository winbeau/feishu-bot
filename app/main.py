import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from app.backends.dify import DifyBackend
from app.core.dedup import DeduplicationStore
from app.core.gateway import Gateway
from app.core.models import MessageType, UnifiedMessage
from app.core.session import ConversationSummaryStore, SessionStore
from app.platforms.feishu import FeishuAdapter
from app.services.dify_files import DifyFileUploadError, DifyFileUploadService
from app.services.feishu_files import FeishuFileDownloadError, FeishuFileService
from app.services.file_parser import FileParserService

logger = logging.getLogger(__name__)
load_dotenv()

REQUIRED_ENV_VARS = (
    "DIFY_API_KEY",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_VERIFICATION_TOKEN",
)


def validate_required_configuration() -> None:
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "missing required environment variables: " + ", ".join(missing)
        )


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    validate_required_configuration()
    yield


app = FastAPI(lifespan=lifespan)


def get_feishu_adapter() -> FeishuAdapter:
    return getattr(app.state, "feishu_adapter", FeishuAdapter())


def get_gateway():
    gateway = getattr(app.state, "gateway", None)
    if gateway is None:
        gateway = Gateway(DifyBackend(), summary_store=ConversationSummaryStore())
        app.state.gateway = gateway
    return gateway


def get_deduplication_store() -> DeduplicationStore:
    store = getattr(app.state, "deduplication_store", None)
    if store is None:
        store = DeduplicationStore()
        app.state.deduplication_store = store
    return store


def get_session_store() -> SessionStore:
    store = getattr(app.state, "session_store", None)
    if store is None:
        store = SessionStore()
        app.state.session_store = store
    return store


def get_feishu_file_service() -> FeishuFileService:
    service = getattr(app.state, "feishu_file_service", None)
    if service is None:
        service = FeishuFileService()
        app.state.feishu_file_service = service
    return service


def get_file_parser_service() -> FileParserService:
    service = getattr(app.state, "file_parser_service", None)
    if service is None:
        service = FileParserService()
        app.state.file_parser_service = service
    return service


def get_dify_file_upload_service() -> DifyFileUploadService:
    service = getattr(app.state, "dify_file_upload_service", None)
    if service is None:
        service = DifyFileUploadService()
        app.state.dify_file_upload_service = service
    return service


def get_health_backends() -> dict[str, Any]:
    backends = getattr(app.state, "health_backends", None)
    if backends is None:
        return {"dify": DifyBackend()}
    return dict(backends)


@app.get("/health", response_model=None)
async def health() -> JSONResponse:
    backend_statuses: dict[str, bool] = {}
    for name, backend in get_health_backends().items():
        try:
            backend_statuses[name] = bool(await backend.health_check())
        except Exception:
            backend_statuses[name] = False

    ok = all(backend_statuses.values())
    return JSONResponse(
        {"ok": ok, "backends": backend_statuses},
        status_code=200 if ok else 503,
    )


@app.post("/feishu/webhook", response_model=None)
async def feishu_webhook(request: Request) -> Response | dict[str, bool]:
    adapter = get_feishu_adapter()

    challenge_response = await adapter.handle_challenge(request)
    if challenge_response is not None:
        return challenge_response

    if not await adapter.verify_signature(request):
        raise HTTPException(status_code=401, detail="invalid verification token")

    raw = await request.json()
    incoming = await adapter.parse_incoming(raw)
    if incoming.message_id:
        is_first_delivery = await get_deduplication_store().mark_seen(
            incoming.message_id
        )
        if not is_first_delivery:
            return {"ok": True}

    feishu_receive_id = _extract_feishu_chat_id(raw) or incoming.session_id
    incoming.session_id = await get_session_store().get_or_create_session_id(
        incoming.platform,
        incoming.user_id,
    )

    if incoming.attachments:
        try:
            await _process_feishu_attachments(incoming)
        except FeishuFileDownloadError:
            logger.exception(
                "feishu attachment download failed",
                extra={
                    "event": "feishu_attachment_processing",
                    "message_id": incoming.message_id,
                },
            )
            await _send_feishu_text_reply(
                adapter,
                incoming,
                feishu_receive_id,
                "文件下载失败，请稍后重试",
            )
            return {"ok": True}
        except DifyFileUploadError:
            logger.exception(
                "dify attachment upload failed",
                extra={
                    "event": "dify_attachment_processing",
                    "message_id": incoming.message_id,
                },
            )
            await _send_feishu_text_reply(
                adapter,
                incoming,
                feishu_receive_id,
                "图片处理失败，请稍后重试",
            )
            return {"ok": True}

    reply = await get_gateway().route(incoming)
    await _send_feishu_text_reply(adapter, incoming, feishu_receive_id, reply)
    return {"ok": True}


async def _process_feishu_attachments(incoming: UnifiedMessage) -> None:
    file_type = "image" if incoming.message_type is MessageType.IMAGE else "file"
    if not incoming.message_id:
        raise FeishuFileDownloadError("attachment message is missing message_id")

    file_service = get_feishu_file_service()
    parser_service = get_file_parser_service()
    for attachment in incoming.attachments:
        await file_service.download_attachment(
            incoming.message_id,
            attachment,
            file_type,
        )
        if incoming.message_type is MessageType.IMAGE:
            await get_dify_file_upload_service().upload_attachment(
                attachment,
                incoming.user_id,
                dify_file_type="image",
            )
        elif incoming.message_type is MessageType.FILE:
            parser_service.parse_attachment(attachment)


async def _send_feishu_text_reply(
    adapter: FeishuAdapter,
    incoming: UnifiedMessage,
    receive_id: str,
    content: str,
) -> None:
    outgoing = UnifiedMessage(
        platform=incoming.platform,
        message_type=MessageType.TEXT,
        session_id=receive_id,
        user_id=incoming.user_id,
        content=content,
        raw=incoming.raw,
    )
    await adapter.send_message(outgoing)


def _extract_feishu_chat_id(raw: dict[str, Any]) -> str | None:
    chat_id = raw.get("event", {}).get("message", {}).get("chat_id")
    return chat_id if isinstance(chat_id, str) else None
