"""
utils/perm_store.py

Redis permission store — lưu quyền truy cập của session dưới dạng Redis Set.

Thiết kế:
  Key  : "perm:{session_id}:instances"   → Redis Set{instance_name}
  Key  : "perm:{session_id}:ma:{instance}"→ Redis Set{ma_chuc_nang}
  TTL  : 8 giờ (SESSION_PERM_TTL)

Ưu điểm so với lưu JSON dict trong PostgreSQL:
  - SISMEMBER O(1) — check quyền không cần đọc toàn bộ dict
  - LLM không bao giờ thấy danh sách collections
  - Không tốn token PostgreSQL roundtrip mỗi tool call
  - TTL tự expire, không cần cleanup

modata-mcp đọc từ Redis thay vì PostgreSQL.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

_PREFIX_INST = "perm:{sid}:instances"
_PREFIX_MA   = "perm:{sid}:ma:{inst}"
def _get_ttl() -> int:
    try:
        from app.core.config import settings
        return settings.SESSION_PERM_TTL
    except Exception:
        return 28800   # fallback 8 giờ


def _key_inst(session_id: str) -> str:
    return f"perm:{session_id}:instances"

def _key_ma(session_id: str, instance_name: str) -> str:
    return f"perm:{session_id}:ma:{instance_name}"


def _get_redis():
    try:
        import redis as redis_lib
        from app.core.config import settings
        r = redis_lib.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        r.ping()
        return r
    except Exception as e:
        logger.warning("Redis unavailable for perm_store: %s", e)
        return None


def save_permission(
    session_id:  str,
    accessible:  dict[str, list[str]],  # {instance_name: [ma_chuc_nang]}
):
    """
    Lưu quyền vào Redis Sets.
    Gọi 1 lần khi user login/request đầu tiên.
    Pipeline để ghi tất cả trong 1 roundtrip.
    """
    r = _get_redis()
    if r is None:
        return

    if not accessible:
        return

    try:
        pipe = r.pipeline(transaction=False)

        # Set chứa tất cả instance_name user được phép
        inst_key = _key_inst(session_id)
        pipe.delete(inst_key)
        pipe.sadd(inst_key, *accessible.keys())
        pipe.expire(inst_key, _get_ttl())

        # Mỗi instance_name → Set chứa ma_chuc_nang tương ứng
        for inst, ma_list in accessible.items():
            ma_key = _key_ma(session_id, inst)
            pipe.delete(ma_key)
            if ma_list:
                pipe.sadd(ma_key, *ma_list)
            else:
                # Không có ma_chuc_nang → dùng sentinel để biết inst có quyền
                pipe.sadd(ma_key, "__any__")
            pipe.expire(ma_key, _get_ttl())

        pipe.execute()
        logger.debug(
            "Saved perm to Redis: session=%s, %d collections",
            session_id, len(accessible),
        )
    except Exception as e:
        logger.warning("perm_store.save error: %s", e)


def can_access(session_id: str, instance_name: str) -> bool:
    """
    O(1) check — SISMEMBER Redis Set.
    Không đọc toàn bộ dict, không tốn token context.
    """
    r = _get_redis()
    if r is None:
        return False
    try:
        return bool(r.sismember(_key_inst(session_id), instance_name))
    except Exception as e:
        logger.warning("perm_store.can_access error: %s", e)
        return False


def get_ma_chuc_nang(session_id: str, instance_name: str) -> list[str]:
    """
    Lấy list ma_chuc_nang cho (session, instance_name).
    Dùng để get_schema_info trong schema_cache.
    """
    r = _get_redis()
    if r is None:
        return []
    try:
        ma_set = r.smembers(_key_ma(session_id, instance_name))
        return [m for m in ma_set if m != "__any__"]
    except Exception as e:
        logger.warning("perm_store.get_ma error: %s", e)
        return []


def get_all_instances(session_id: str) -> list[str]:
    """
    Lấy tất cả instance_name user được phép.
    Dùng cho list_accessible_collections.
    """
    r = _get_redis()
    if r is None:
        return []
    try:
        return list(r.smembers(_key_inst(session_id)))
    except Exception as e:
        logger.warning("perm_store.get_all error: %s", e)
        return []


def delete_permission(session_id: str):
    """Xoá quyền khi session expired hoặc user logout."""
    r = _get_redis()
    if r is None:
        return
    try:
        # Lấy tất cả instance để xoá cả ma_chuc_nang keys
        instances = r.smembers(_key_inst(session_id))
        keys = [_key_inst(session_id)] + [_key_ma(session_id, i) for i in instances]
        r.delete(*keys)
    except Exception as e:
        logger.warning("perm_store.delete error: %s", e)