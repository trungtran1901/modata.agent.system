"""
workflow/hitc_agent.py  (v2.1)

HITC AgentOS — Single AgentOS entry point cho toàn bộ hệ thống.

FIX v2.1: Context injection qua FastAPI middleware + team wrapper.

Giải pháp:
  - Dùng BaseHTTPMiddleware để chặn /teams/{id}/runs requests
  - FastAPI tự động cache body, có thể gọi await request.json() multiple times
  - Truy cập request.state để lấy user từ auth middleware
  - Nếu không có session_id nhưng có user, tạo session mới
  - Inject context vào agents trước khi AgentOS xử lý
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.registry import Registry
from agno.tools.mcp import MCPTools
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from utils.qwen_model import QwenOpenAILike as OpenAILike
from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store
from workflow.agentosagno_hooks import get_context_injecting_agent_os

# ── Import teams ─────────────────────────────────────────────
from workflow.hrm_team import (
    _get_hrm_team,
    _get_agents_cache as _hrm_agents_cache,
    _inject_session_context as _hrm_inject,
    _augmented_query as _hrm_aug_query,
    _get_routed_agent_id as _hrm_routed_id,
    chat_with_hrm_team,
    stream_with_hrm_team,
    AGENT_ID_EMPLOYEE,
)
from workflow.document_team import (
    _get_document_team,
    _get_agents_cache as _doc_agents_cache,
    _inject_session_context as _doc_inject,
    _augmented_query as _doc_aug_query,
    chat_with_document_team,
    stream_with_document_team,
    AGENT_ID_DOC_READER,
    TEAM_ID_DOCUMENT,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CONTEXT INJECTORS — map team_id → inject function
# ─────────────────────────────────────────────────────────────

_CONTEXT_INJECTORS = {
    "hrm-team": _hrm_inject,
    "document-team": _doc_inject,
}


def _reconstruct_user_context(session_id: str) -> Optional[UserPermissionContext]:
    """Lấy UserPermissionContext từ session_store."""
    if not session_id:
        return None
    try:
        ctx = session_store.get_context(session_id)
        if not ctx:
            logger.warning(
                "[AgentOS Middleware] session_store MISS: session=%s — "
                "Đảm bảo auth middleware đã gọi session_store.set_context() trước.",
                session_id,
            )
            return None
        return UserPermissionContext(
            user_id=ctx.get("user_id", ""),
            username=ctx.get("username", "unknown"),
            company_code=ctx.get("company_code", ""),
            don_vi_code=ctx.get("don_vi_code", ""),
            accessible_instance_names=ctx.get("accessible", []),
        )
    except Exception as e:
        logger.warning("[AgentOS Middleware] Error reconstructing context: %s", e)
        return None


# ─────────────────────────────────────────────────────────────
# MIDDLEWARE — pure ASGI, inject context trước khi AgentOS xử lý
# ─────────────────────────────────────────────────────────────

class AgentOSContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware inject context vào AgentOS team runs.
    
    Dùng BaseHTTPMiddleware để truy cập request.state (chứa user context từ auth middleware).
    FastAPI sẽ tự động handle body replay, không cần manual receive wrapper.
    
    session_id được lấy từ (theo thứ tự ưu tiên):
      1. Header: X-Session-Id
      2. Header: Authorization: Bearer <token> (nếu ngắn)
      3. Request body: session_id, thread_id, run_id
      
    Nếu không tìm thấy session_id nhưng có user từ auth, tạo session mới.
    """

    async def dispatch(self, request: Request, call_next):
        method = request.method
        path = request.url.path

        # Chỉ xử lý AgentOS team run endpoints
        if not (method == "POST" and "/teams/" in path and path.endswith("/runs")):
            return await call_next(request)

        # --- Tách team_id từ path ---
        try:
            parts = path.strip("/").split("/")
            team_id = parts[parts.index("teams") + 1]
        except (ValueError, IndexError):
            return await call_next(request)

        logger.info("[AgentOS Middleware] ▶ Intercepted: POST %s | team=%s", path, team_id)

        # --- Lấy body (FastAPI tự cache, có thể gọi multiple times) ---
        try:
            body_json = await request.json()
        except Exception:
            body_json = {}

        # --- Tìm session_id ---
        session_id = self._extract_session_id(request, body_json)
        
        # --- Nếu không có session_id nhưng có user, tạo session mới ---
        user_context = getattr(request.state, "user", None)
        if not session_id and user_context:
            # Tạo session_id mới và lưu context
            session_id = str(uuid.uuid4())
            session_store.save_context(
                session_id=session_id,
                user_id=user_context.user_id,
                username=user_context.username,
                company_code=user_context.company_code,
                accessible=user_context.accessible_instance_names or {},
            )
            logger.info(
                "[AgentOS Middleware] Created session: session=%s user=%s",
                session_id, user_context.username,
            )
        
        logger.info(
            "[AgentOS Middleware] session_id='%s' | body_keys=%s",
            session_id or "NOT FOUND", list(body_json.keys()),
        )

        # --- Inject context vào agents ---
        if session_id and team_id in _CONTEXT_INJECTORS:
            user_context = _reconstruct_user_context(session_id)
            if user_context:
                try:
                    _CONTEXT_INJECTORS[team_id](session_id, user_context)
                    logger.info(
                        "[AgentOS Middleware] ✓ Injected: team=%s session=%s user=%s",
                        team_id, session_id, user_context.username,
                    )
                except Exception as e:
                    logger.warning(
                        "[AgentOS Middleware] ✗ Injection error: team=%s error=%s",
                        team_id, e, exc_info=True,
                    )
            else:
                logger.warning(
                    "[AgentOS Middleware] ⚠ No user_context for session=%s",
                    session_id,
                )
        else:
            if team_id not in _CONTEXT_INJECTORS:
                logger.info("[AgentOS Middleware] No injector for team='%s'", team_id)
            else:
                logger.warning(
                    "[AgentOS Middleware] ⚠ No session_id found for team=%s",
                    team_id,
                )

        return await call_next(request)

    @staticmethod
    def _extract_session_id(request: Request, body: dict) -> str:
        """
        Tìm session_id từ nhiều nguồn (thứ tự ưu tiên):
        1. Header X-Session-Id
        2. Body: session_id, thread_id, run_id, conversation_id, user_id
        """
        headers = request.headers

        # 1. Header X-Session-Id (custom header)
        val = headers.get("x-session-id", "")
        if val:
            return val

        # 2. Body JSON fields
        for key in ("session_id", "thread_id", "run_id", "conversation_id", "user_id"):
            val = body.get(key, "")
            if val:
                return str(val)

        return ""


# ─────────────────────────────────────────────────────────────
# TEAM ROUTING — phát hiện team từ query
# ─────────────────────────────────────────────────────────────

_HRM_KEYWORDS = {
    "nhân viên", "nhan vien", "hồ sơ", "ho so", "thâm niên", "tham nien",
    "thông tin của tôi", "thong tin cua toi", "danh sách nhân viên",
    "nghỉ phép", "nghi phep", "ngày nghỉ", "ngay nghi", "ngày lễ", "ngay le",
    "lịch nghỉ", "lich nghi", "nghỉ lễ", "nghi le", "phép năm", "phep nam",
    "nghỉ tuần", "nghi tuan", "thứ 7", "chủ nhật",
    "đơn xin", "don xin", "xin nghỉ", "đơn nghỉ", "don nghi", "đơn phép",
    "đi muộn", "di muon", "về sớm", "ve som", "làm việc từ xa", "remote",
    "công tác", "cong tac", "đơn từ", "don tu",
    "chấm công", "cham cong", "giờ vào", "gio vao", "giờ ra", "gio ra",
    "check-in", "check in", "hôm nay vào", "hom nay vao", "bảng công",
    "bảng chấm công", "xuất excel", "xuat excel", "tổng hợp công",
    "tổng hợp tháng", "gửi mail bảng công",
}

_DOC_KEYWORDS = {
    "đọc văn bản", "doc van ban", "tóm tắt", "tom tat", "summarize",
    "phân tích văn bản", "phan tich van ban", "trích xuất", "trich xuat",
    "extract", "json schema", "output json", "schema json", "điền form",
    "điền template", "qa văn bản", "câu hỏi về văn bản",
    "hợp đồng", "hop dong", "báo cáo", "bao cao", "quy định", "quy dinh",
    "nội dung văn bản", "noi dung van ban", "văn bản", "van ban",
}


def _detect_team(query: str) -> str:
    q_lower = query.lower()
    hrm_score = sum(1 for kw in _HRM_KEYWORDS if kw in q_lower)
    doc_score = sum(1 for kw in _DOC_KEYWORDS if kw in q_lower)
    if hrm_score > doc_score:
        return "hrm"
    if doc_score > hrm_score:
        return "document"
    return "hrm"


# ─────────────────────────────────────────────────────────────
# HITC AGENTOS FACTORY
# ─────────────────────────────────────────────────────────────

def _build_agent_os(base_app: Optional[FastAPI] = None) -> AgentOS:
    """Tạo AgentOS instance (không cache — để tránh rebuild conflict)."""
    hrm_team = _get_hrm_team()
    doc_team = _get_document_team()

    try:
        db = PostgresDb(db_url=settings.AGENTOSAGNO_DB_URL)
        logger.info("✓ AgentOS DB: PostgreSQL (%s)", settings.AGENTOSAGNO_DB_NAME)
    except Exception as e:
        logger.warning("⚠ AgentOS DB fallback SQLite: %s", e)
        db = SqliteDb(table_name="hitc_agentosagno_sessions")

    registry = Registry(
        name="HITC Registry",
        tools=[MCPTools(url=settings.MCP_GATEWAY_URL)],
        models=[
            OpenAILike(
                id=settings.LLM_MODEL,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "sk-",
            )
        ],
        dbs=[db],
    )

    kwargs = dict(
        name="HITC AgentOS",
        description=(
            "Hệ thống AI đa tác nhân HITC — điều phối HRM Team và "
            "Document Intelligence Team để xử lý mọi yêu cầu nội bộ."
        ),
        teams=[hrm_team, doc_team],
        db=db,
        registry=registry,
    )
    if base_app is not None:
        kwargs["base_app"] = base_app

    agent_os = AgentOS(**kwargs)
    
    # ✨ Wrap teams with context injection (double layer: middleware + team wrapper)
    # Middleware handles ASGI level, team wrapper handles runtime level
    agent_os = get_context_injecting_agent_os(agent_os)
    
    logger.info("✓ HITC AgentOS initialized (2 teams: HRM + Document)")
    logger.info("✓ Control Plane endpoint: %s", settings.AGENTOSAGNO_ENDPOINT)
    return agent_os


def create_hitc_agent_os_app(base_app: Optional[FastAPI] = None) -> FastAPI:
    """
    Tạo HITC AgentOS FastAPI app với context injection middleware.

    Dùng trong main.py:
        from workflow.hitc_agent import create_hitc_agent_os_app
        app = create_hitc_agent_os_app(base_app=app)

    Endpoints tự động:
        POST /teams/hrm-team/runs
        POST /teams/document-team/runs

    Để context injection hoạt động, client PHẢI gửi session_id qua:
        Header: X-Session-Id: <session_id>
        HOẶC Body JSON: { "session_id": "<session_id>", "message": "..." }
    """
    # ✅ Thêm middleware VÀO base_app TRƯỚC khi AgentOS sử dụng
    # Điều này đảm bảo middleware intercept tất cả requests
    if base_app is not None:
        base_app.add_middleware(AgentOSContextMiddleware)
        logger.info("[AgentOS Setup] Added middleware to base_app (will run FIRST)")
    
    agent_os = _build_agent_os(base_app=base_app)
    app = agent_os.get_app()

    # ✅ Nếu không có base_app, thêm middleware vào app sau get_app()
    if base_app is None:
        app.add_middleware(AgentOSContextMiddleware)
        logger.info("[AgentOS Setup] Added middleware to AgentOS app")

    logger.info(
        "✓ AgentOSContextMiddleware registered — "
        "POST /teams/{id}/runs sẽ inject context từ auth user"
    )
    return app


# ─────────────────────────────────────────────────────────────
# UNIFIED CHAT BRIDGE (không đổi — đã hoạt động tốt)
# ─────────────────────────────────────────────────────────────

async def chat_with_hitc(
    query:            str,
    user:             UserPermissionContext,
    session_id:       str,
    history:          list[dict],
    document_content: str = "",
    output_schema:    str = "",
    role:             str = "",
    force_team:       str = "",
) -> dict:
    """Unified chat bridge — tự phát hiện team và dispatch."""
    team_choice = force_team or _detect_team(query)
    logger.info("HITC dispatch: team=%s session=%s user=%s", team_choice, session_id, user.username)

    if team_choice == "document":
        return await chat_with_document_team(
            query=query, user=user, session_id=session_id, history=history,
            document_content=document_content, output_schema=output_schema, role=role,
        )
    return await chat_with_hrm_team(
        query=query, user=user, session_id=session_id, history=history,
    )


async def stream_with_hitc(
    query:            str,
    user:             UserPermissionContext,
    session_id:       str,
    history:          list[dict],
    document_content: str = "",
    output_schema:    str = "",
    role:             str = "",
    force_team:       str = "",
):
    """SSE streaming bridge — dispatch đến đúng team."""
    team_choice = force_team or _detect_team(query)
    logger.info("HITC SSE dispatch: team=%s session=%s user=%s", team_choice, session_id, user.username)

    if team_choice == "document":
        async for event in stream_with_document_team(
            query=query, user=user, session_id=session_id, history=history,
            document_content=document_content, output_schema=output_schema, role=role,
        ):
            yield event
    else:
        async for event in stream_with_hrm_team(
            query=query, user=user, session_id=session_id, history=history,
        ):
            yield event