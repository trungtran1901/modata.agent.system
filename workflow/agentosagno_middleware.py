"""
workflow/agentosagno_middleware.py

Middleware to inject session context into AgentOS team requests.

Problem:
  When requests come through `/teams/{id}/runs`, AgentOS doesn't have
  UserPermissionContext, so we can't pass it to context injection.

Solution:
  1. Extract session_id and user_id from request body
  2. Look up user context from session_store using session_id
  3. Inject context before team execution

FIX: Replaced BaseHTTPMiddleware with pure ASGI middleware to avoid
     "RuntimeError: Unexpected message received: http.request".
     BaseHTTPMiddleware consumes the request body and cannot reliably
     replay it for downstream middleware. Pure ASGI middleware reads
     the body manually and replays it via a closure.

Usage:
  Add to hitc_agent.py before creating AgentOS
"""

import json
import logging
from typing import Optional, Dict, Any

from starlette.types import ASGIApp, Receive, Scope, Send

from workflow.session import session_store

logger = logging.getLogger(__name__)


class SessionContextMiddleware:
    """
    Pure ASGI middleware to preserve session context through AgentOS requests.

    Replaces BaseHTTPMiddleware to avoid body double-read bug:
      - BaseHTTPMiddleware consumes body stream → downstream gets empty body
      - Pure ASGI reads chunks → replays via replay_receive closure

    This ensures that session_id passed in request body is available
    for context injection in team execution.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # Only handle HTTP, only handle /teams/ paths
        if scope["type"] != "http" or "/teams/" not in scope.get("path", ""):
            await self.app(scope, receive, send)
            return

        if scope.get("method", "") != "POST":
            await self.app(scope, receive, send)
            return

        # --- Read body once, accumulating all chunks ---
        body_chunks = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)
        body_bytes = b"".join(body_chunks)

        # --- Parse JSON ---
        body_json = {}
        try:
            body_json = json.loads(body_bytes) if body_bytes else {}
        except Exception:
            pass

        # --- Extract session_id / user_id ---
        session_id = body_json.get("session_id", "")
        user_id = body_json.get("user_id", "")

        logger.debug(
            "[SessionContextMiddleware] Captured session_id=%s user_id=%s",
            session_id, user_id,
        )

        # --- Store in scope["state"] for downstream use ---
        scope.setdefault("state", {})
        scope["state"]["team_session_id"] = session_id
        scope["state"]["team_user_id"] = user_id

        # --- Replay body so downstream middleware/routes can read it ---
        async def replay_receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        await self.app(scope, replay_receive, send)


def extract_session_from_scope(scope: Scope) -> tuple[str, str]:
    """
    Extract session_id and user_id from ASGI scope state.

    Returns:
        (session_id, user_id) tuple
    """
    state = scope.get("state", {})
    session_id = state.get("team_session_id", "")
    user_id = state.get("team_user_id", "")
    return session_id, user_id


def get_user_context_from_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve user context from session store.

    Args:
        session_id: Session ID to lookup

    Returns:
        User context dict or None
    """
    try:
        if not session_id:
            return None
        context = session_store.get_context(session_id)
        return context
    except Exception as e:
        logger.debug("[SessionContextMiddleware] Error getting user context: %s", e)
        return None