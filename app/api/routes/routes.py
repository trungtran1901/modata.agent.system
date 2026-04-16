"""app/api/routes/routes.py — /chat endpoint with Agno AgentOS"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from utils.permission import PermissionService, UserPermissionContext
from workflow.agents import chat_with_agentosagno
from workflow.session import session_store

logger = logging.getLogger(__name__)

_perm_svc = PermissionService()

chat_router = APIRouter(prefix="/chat", tags=["Chat"])


# ── Auth dependency ───────────────────────────────────────────

async def get_user(
    authorization: Optional[str] = Header(None),
    x_api_key:     Optional[str] = Header(None, alias="X-Api-Key"),
) -> UserPermissionContext:
    """
    Hỗ trợ 2 phương thức xác thực (ưu tiên theo thứ tự):
      1. Bearer JWT  — header: Authorization: Bearer <token>
      2. API Key     — header: X-Api-Key: <api_key>
    """
    # Phương thức 1: Bearer JWT
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Header Authorization không hợp lệ. Dùng: Bearer <token>")
        try:
            return await _perm_svc.build_context(authorization)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    # Phương thức 2: API Key
    if x_api_key:
        try:
            return _perm_svc.build_context_from_api_key(x_api_key)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    raise HTTPException(
        401,
        "Cần xác thực. Truyền 'Authorization: Bearer <token>' hoặc 'X-Api-Key: <api_key>'",
    )


# ── Request / Response models ─────────────────────────────────

class ChatRequest(BaseModel):
    query:      str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    answer:     str
    agents_used: Optional[list[str]] = None
    agent_results: Optional[list[Any]] = None
    metrics: Optional[dict[str, Any]] = None


# ── Endpoints ─────────────────────────────────────────────────

@chat_router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Chat endpoint — Agno AgentOS Multi-Agent System.

    Xác thực qua:
    - `Authorization: Bearer <token>` (Keycloak JWT)
    - `X-Api-Key: <api_key>` (API Key trong MongoDB)

    Automatically routes query to specialized agents:
    - CheckinAgent (chấm công, giờ vào ra)
    - DataQueryAgent (nhân viên, hợp đồng, phép)
    - AnalyticsAgent (thống kê, count, group by)
    - CoordinatorAgent (phối hợp)

    Returns:
      - answer: Final answer from agents
      - agents_used: List of agents that processed the query
      - agent_results: Individual results from each agent
      - metrics: Performance metrics
    """
    try:
        sid     = req.session_id or str(uuid.uuid4())
        history = session_store.load(sid)

        result = await chat_with_agentosagno(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
        )

        return ChatResponse(
            session_id=result.get("session_id", sid),
            answer=str(result.get("answer", "")),
            agents_used=result.get("agents_used", []),
            agent_results=result.get("agent_results", []),
            metrics=result.get("metrics", {}),
        )
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@chat_router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    """Lấy lịch sử hội thoại của session."""
    messages = session_store.load(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@chat_router.delete("/session/{session_id}")
async def clear_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    """Xoá lịch sử hội thoại của session."""
    session_store.save(session_id, user.user_id, user.username, [])
    return {"session_id": session_id, "status": "cleared"}