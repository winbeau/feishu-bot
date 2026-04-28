import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.backends.dify import DifyBackend
from app.core.dedup import DeduplicationStore
from app.core.models import UnifiedMessage
from app.platforms.feishu import FeishuAdapter

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
        raise HTTPException(status_code=503, detail="gateway is not configured")
    return gateway


def get_deduplication_store() -> DeduplicationStore:
    store = getattr(app.state, "deduplication_store", None)
    if store is None:
        store = DeduplicationStore()
        app.state.deduplication_store = store
    return store


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

    reply = await get_gateway().route(incoming)
    outgoing = UnifiedMessage(
        platform=incoming.platform,
        message_type=incoming.message_type,
        session_id=incoming.session_id,
        user_id=incoming.user_id,
        content=reply,
        raw=incoming.raw,
    )
    await adapter.send_message(outgoing)
    return {"ok": True}
