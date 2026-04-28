from fastapi import FastAPI, HTTPException, Request, Response

from app.core.models import UnifiedMessage
from app.platforms.feishu import FeishuAdapter

app = FastAPI()


def get_feishu_adapter() -> FeishuAdapter:
    return getattr(app.state, "feishu_adapter", FeishuAdapter())


def get_gateway():
    gateway = getattr(app.state, "gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="gateway is not configured")
    return gateway


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
