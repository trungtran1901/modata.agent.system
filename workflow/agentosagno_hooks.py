"""
workflow/agentosagno_hooks.py

AgentOS Custom Hooks for Context Injection

When requests come through AgentOS REST endpoints (/teams/{id}/runs),
we need to inject session context into agents before execution.
This ensures MCP tools have access to session_id and user info.

Why: AgentOS directly calls team.arun() without context injection.
      Direct calls (chat_with_hrm_team) call _inject_session_context() before team.arun().
      This causes "Function not found" errors when MCP tools expect session_id.

Solution: Enhance teams with context injection before passing to AgentOS.

Debug checklist (nếu vẫn thấy "Function not found"):
  1. Bật INFO log: logging.getLogger("workflow.agentosagno_hooks").setLevel(logging.INFO)
  2. Kiểm tra "[ContextInjection] kwargs keys" — session_id có trong đó không?
  3. Kiểm tra "[ContextInjection] session_store.get_context" — context có được lưu trước không?
  4. Kiểm tra "[ContextInjection] ✓ Injected" — injection có thực sự chạy không?
"""

import inspect
import inspect
import logging
from typing import Any, AsyncGenerator, Optional

from agno.team import Team

from utils.permission import UserPermissionContext
from workflow.session import session_store
from workflow.hrm_team import _inject_session_context as _hrm_inject
from workflow.document_team import _inject_session_context as _doc_inject

logger = logging.getLogger(__name__)

# Bật INFO log cho module này để debug dễ hơn
# (Xoá dòng này khi production ổn định)
logger.setLevel(logging.INFO)

# Map team_id to injection function
_CONTEXT_INJECTORS = {
    "hrm-team": _hrm_inject,
    "document-team": _doc_inject,
}


def reconstruct_user_context_from_session(session_id: str) -> Optional[UserPermissionContext]:
    """
    Reconstruct UserPermissionContext from session store.
    """
    if not session_id:
        return None

    try:
        context = session_store.get_context(session_id)

        # --- DEBUG: log raw context để kiểm tra ---
        if context:
            logger.info(
                "[ContextInjection] session_store hit: session=%s keys=%s",
                session_id, list(context.keys()),
            )
        else:
            logger.warning(
                "[ContextInjection] session_store MISS: session=%s — "
                "context chưa được set trước khi AgentOS gọi team.arun(). "
                "Kiểm tra xem login/auth middleware có gọi session_store.set_context() không.",
                session_id,
            )
            return None

        user_context = UserPermissionContext(
            user_id=context.get('user_id', ''),
            username=context.get('username', 'unknown'),
            company_code=context.get('company_code', ''),
            don_vi_code=context.get('don_vi_code', ''),
            accessible_instance_names=context.get('accessible', []),
        )

        logger.info(
            "[ContextInjection] Reconstructed: user=%s company=%s don_vi=%s",
            user_context.username, user_context.company_code, user_context.don_vi_code,
        )
        return user_context

    except Exception as e:
        logger.warning("[ContextInjection] Error reconstructing context: %s", e, exc_info=True)
        return None


def _extract_session_id(kwargs: dict) -> str:
    """
    AgentOS có thể truyền session_id dưới nhiều tên khác nhau.
    Thử lần lượt các key phổ biến.
    """
    for key in ("session_id", "run_id", "thread_id", "conversation_id", "request_id"):
        val = kwargs.get(key, "")
        if val:
            logger.info("[ContextInjection] Found session_id via key='%s': %s", key, val)
            return str(val)
    return ""


def wrap_team_with_context_injection(team: Team, team_id: str) -> Team:
    """
    Wrap a Team with context injection.

    Ensures that when AgentOS calls team.arun(), session context is injected
    into MCP tools before execution — fixing "Function not found" errors.
    """
    if team_id not in _CONTEXT_INJECTORS:
        logger.info("[ContextInjection] No injector registered for team='%s', skipping", team_id)
        return team

    original_arun = team.arun

    async def arun_with_context_injection(message: str, **kwargs) -> Any:
        """Wrapper that injects context before team execution."""

        # --- STEP 1: Log tất cả kwargs để biết AgentOS truyền gì ---
        logger.info(
            "[ContextInjection] ▶ team.arun() called: team=%s | message_len=%d | kwargs_keys=%s",
            team_id, len(message or ""), list(kwargs.keys()),
        )

        # --- STEP 2: Tìm session_id từ kwargs (thử nhiều key) ---
        session_id = _extract_session_id(kwargs)

        if not session_id:
            logger.warning(
                "[ContextInjection] ⚠ No session_id found in kwargs=%s. "
                "MCP tools sẽ KHÔNG có context → 'Function not found' có thể xảy ra. "
                "Kiểm tra AgentOS có truyền session_id/run_id vào team.arun() không.",
                list(kwargs.keys()),
            )
            # Vẫn tiếp tục — không block request
            return await original_arun(message, **kwargs)

        # --- STEP 3: Lấy user_context từ session_store ---
        user_context = reconstruct_user_context_from_session(session_id)

        if not user_context:
            logger.warning(
                "[ContextInjection] ⚠ session_id='%s' found but user_context is None. "
                "Nguyên nhân: session chưa được lưu vào session_store. "
                "Đảm bảo auth middleware gọi session_store.set_context(session_id, {...}) "
                "trước khi AgentOS nhận request.",
                session_id,
            )
            return await original_arun(message, **kwargs)

        # --- STEP 4: Inject context ---
        try:
            injector = _CONTEXT_INJECTORS[team_id]
            injector(session_id, user_context)
            logger.info(
                "[ContextInjection] ✓ Injected: team=%s session=%s user=%s company=%s",
                team_id, session_id, user_context.username, user_context.company_code,
            )
        except Exception as e:
            logger.warning(
                "[ContextInjection] ✗ Injection failed: team=%s session=%s error=%s",
                team_id, session_id, e, exc_info=True,
            )
            # Vẫn tiếp tục — injection thất bại không nên block request

        # --- STEP 5: Gọi original arun ---
        # Call original_arun but do NOT eagerly await if it returns an async generator
        try:
            result = original_arun(message, **kwargs)
        except Exception:
            # If calling original_arun raises synchronously, re-raise after logging
            logger.exception("[ContextInjection] Error calling original team.arun()")
            raise

        # If result is an async generator (streaming), return it directly so Starlette can iterate it.
        if inspect.isasyncgen(result) or hasattr(result, "__aiter__"):
            logger.info("[ContextInjection] ◀ team.arun() returned async generator (streaming): team=%s session=%s", team_id, session_id)
            return result

        # If result is awaitable (coroutine), await and return the final value.
        if inspect.isawaitable(result):
            final = await result
            logger.info("[ContextInjection] ◀ team.arun() completed (awaitable): team=%s session=%s", team_id, session_id)
            return final

        # Otherwise it's a regular value; return as-is.
        logger.info("[ContextInjection] ◀ team.arun() completed (sync return): team=%s session=%s", team_id, session_id)
        return result

    team.arun = arun_with_context_injection
    logger.info("[ContextInjection] ✨ Team '%s' wrapped with context injection", team_id)
    return team


def get_context_injecting_agent_os(agent_os) -> Any:
    """
    Wrap all teams inside an AgentOS instance with context injection.

    Called once after AgentOS is constructed so that when AgentOS calls
    team.arun(), session context is injected automatically into each team.

    Args:
        agent_os: AgentOS instance

    Returns:
        Same AgentOS instance, with each registered team wrapped.
    """
    if not hasattr(agent_os, 'teams') or not agent_os.teams:
        logger.warning("[ContextInjection] AgentOS has no 'teams' attribute or teams is empty")
        return agent_os

    wrapped_teams = []
    for team in agent_os.teams:
        # Thử lấy team_id từ các attribute phổ biến của Agno Team
        team_id = (
            getattr(team, 'team_id', None)
            or getattr(team, 'id', None)
            or getattr(team, 'name', '')
        )

        # Normalize: "HRM Team" → "hrm-team"
        team_id_normalized = str(team_id).lower().replace(' ', '-')

        matched_id = None
        if team_id in _CONTEXT_INJECTORS:
            matched_id = team_id
        elif team_id_normalized in _CONTEXT_INJECTORS:
            matched_id = team_id_normalized

        logger.info(
            "[ContextInjection] Scanning team: raw_id='%s' normalized='%s' matched='%s'",
            team_id, team_id_normalized, matched_id or "NONE (no injector)",
        )

        if matched_id:
            team = wrap_team_with_context_injection(team, matched_id)

        wrapped_teams.append(team)

    agent_os.teams = wrapped_teams
    logger.info(
        "[ContextInjection] 🚀 AgentOS wrapped: %d team(s), injectors active for: %s",
        len(wrapped_teams), list(_CONTEXT_INJECTORS.keys()),
    )
    return agent_os