"""FastAPI 路由。

所有端點掛載在 /api prefix，由 main.py 的 app.include_router() 載入。

端點：
    POST   /api/sessions                    建立新聊天室
    DELETE /api/sessions/{session_id}        刪除聊天室
    GET    /api/sessions                     列出所有聊天室
    POST   /api/sessions/{session_id}/chat   發送訊息（SSE 串流回覆）
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api.sse import agent_registry, run_agent_turn

router = APIRouter(prefix="/api")


# ── Request / Response schemas ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


# ── Session 管理 ───────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=201)
async def create_session():
    """建立新聊天室，回傳 session_id 與建立時間。"""
    session_id = agent_registry.create()
    meta = agent_registry.get_meta(session_id)
    return {
        "session_id": session_id,
        "created_at": meta["created_at"],
        "mode": "idle",
    }


@router.get("/sessions")
async def list_sessions():
    """列出所有聊天室（session_id、建立時間、當前模式）。"""
    return {"sessions": agent_registry.list_all()}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    """刪除指定聊天室。"""
    if not agent_registry.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    agent_registry.delete(session_id)
    return Response(status_code=204)


# ── Chat（SSE 串流）────────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatRequest):
    """發送訊息，以 SSE text/event-stream 串流回覆。

    用戶輸入 yes / no 也走同一個端點，
    由 session state 判斷當前等待確認的動作。
    """
    if not agent_registry.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    return EventSourceResponse(
        run_agent_turn(session_id, body.message),
        media_type="text/event-stream",
    )
