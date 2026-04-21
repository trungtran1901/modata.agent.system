"""
workflow/session.py

PostgreSQL session store — lưu lịch sử hội thoại và accessible_context
để modata-mcp kiểm tra quyền theo session_id.

Format mới của accessible_context (JSONB):
  {
    "thong_tin_nhan_vien": ["sys_quantrihethong", "nhansu_001"],
    "hop_dong_lao_dong":   ["nhansu_001"]
  }

Khác với format cũ (list[str]):
  ["thong_tin_nhan_vien", "hop_dong_lao_dong"]

Format mới cho phép modata-mcp load đúng view field list theo
(instance_name, ma_chuc_nang) thay vì lấy toàn bộ schema.
"""
from __future__ import annotations

import json
import logging

import psycopg2
from psycopg2.extras import RealDictCursor

from app.core.config import settings

logger = logging.getLogger(__name__)


class SessionStore:

    def __init__(self):
        self._pg = None

    def _conn(self):
        if self._pg is None or self._pg.closed:
            self._pg = psycopg2.connect(settings.PG_DSN)
            self._ensure_table()
        return self._pg

    def _ensure_table(self):
        with self._pg.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rag_sessions (
                    session_id         TEXT        PRIMARY KEY,
                    user_id            TEXT,
                    username           TEXT,
                    messages           JSONB       DEFAULT '[]',
                    accessible_context JSONB       DEFAULT '{}',
                    company_code       TEXT,
                    created_at         TIMESTAMPTZ DEFAULT NOW(),
                    updated_at         TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Graceful migration: thêm cột nếu bảng cũ chưa có
            cur.execute("""
                ALTER TABLE rag_sessions
                ADD COLUMN IF NOT EXISTS accessible_context JSONB DEFAULT '{}'
            """)
        self._pg.commit()

    # ── Context (accessible_context) ──────────────────────────

    def save_context(
        self,
        session_id:  str,
        user_id:     str,
        username:    str,
        accessible:  dict[str, list[str]],   # {instance_name: [ma_chuc_nang]}
        company_code: str,
    ):
        """
        Lưu permission:
          - Redis (primary): Sets cho O(1) SISMEMBER check ở modata-mcp
          - PostgreSQL (fallback): JSON dict cho trường hợp Redis down
        """
        # 1. Redis — primary, O(1) check
        try:
            from utils.perm_store import save_permission
            save_permission(session_id, accessible)
        except Exception as e:
            logger.warning("Redis perm save error: %s", e)

        # 2. PostgreSQL — fallback
        try:
            with self._conn().cursor() as cur:
                cur.execute("""
                    INSERT INTO rag_sessions
                        (session_id, user_id, username, accessible_context, company_code)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        accessible_context = EXCLUDED.accessible_context,
                        company_code       = EXCLUDED.company_code,
                        updated_at         = NOW()
                """, (
                    session_id, user_id, username,
                    json.dumps(accessible),
                    company_code,
                ))
            self._conn().commit()
            logger.debug(
                "Saved context for session %s: %d collections",
                session_id, len(accessible),
            )
        except Exception as e:
            logger.warning("Save context PG error: %s", e)

    def get_context(self, session_id: str) -> dict | None:
        """
        Retrieve user context (user_id, username, accessible_context, company_code)
        from session.

        Returns:
            dict with keys: user_id, username, accessible_context, company_code
            None if session not found

        Used by:
            - MCP tools to validate permissions
            - Agents to retrieve user information
        """
        try:
            with self._conn().cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT user_id, username, accessible_context, company_code
                    FROM rag_sessions
                    WHERE session_id = %s
                """, (session_id,))
                row = cur.fetchone()

            if not row:
                logger.debug("Session context not found: %s", session_id)
                return None

            context = dict(row)
            # Parse accessible_context JSON if stored as string
            if isinstance(context.get("accessible_context"), str):
                try:
                    context["accessible_context"] = json.loads(context["accessible_context"])
                except json.JSONDecodeError:
                    context["accessible_context"] = {}

            logger.debug(
                "Retrieved context for session %s: user=%s company=%s",
                session_id, context.get("username"), context.get("company_code"),
            )
            return context

        except Exception as e:
            logger.warning("Get context PG error: %s", e)
            return None

    # ── Messages ──────────────────────────────────────────────

    def load(self, session_id: str) -> list[dict]:
        try:
            with self._conn().cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT messages FROM rag_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cur.fetchone()
            return row["messages"] if row else []
        except Exception as e:
            logger.warning("Session load error: %s", e)
            return []

    def save(
        self,
        session_id: str,
        user_id:    str,
        username:   str,
        messages:   list[dict],
    ):
        try:
            with self._conn().cursor() as cur:
                cur.execute("""
                    INSERT INTO rag_sessions (session_id, user_id, username, messages)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        messages   = EXCLUDED.messages,
                        updated_at = NOW()
                """, (
                    session_id, user_id, username,
                    json.dumps(messages, ensure_ascii=False),
                ))
            self._conn().commit()
        except Exception as e:
            logger.warning("Session save error: %s", e)


session_store = SessionStore()