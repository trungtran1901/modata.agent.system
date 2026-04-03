"""
app/api/routes/hrm_routes.py

HRM Team API — endpoints dành riêng cho Human Resource Management.

Endpoints:
  POST /hrm/chat               — Chat với HRM Team (qua LLM)
  GET  /hrm/holidays           — Ngày nghỉ lễ trực tiếp (không qua LLM)
  GET  /hrm/leave-types        — Loại nghỉ phép trực tiếp
  GET  /hrm/weekly-off-rules   — Quy định nghỉ tuần trực tiếp
  GET  /hrm/session/{id}       — Lấy lịch sử chat HRM
  DELETE /hrm/session/{id}     — Xoá lịch sử chat HRM
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from utils.permission import PermissionService, UserPermissionContext
from workflow.hrm_team import chat_with_hrm_team
from workflow.session import session_store

logger     = logging.getLogger(__name__)
hrm_router = APIRouter(prefix="/hrm", tags=["HRM Team"])
_perm_svc  = PermissionService()


# ─────────────────────────────────────────────────────────────
# AUTH DEPENDENCY — giống routes.py
# ─────────────────────────────────────────────────────────────

async def get_user(authorization: str = Header(...)) -> UserPermissionContext:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Cần header: Authorization: Bearer <token>")
    try:
        return await _perm_svc.build_context(authorization)
    except PermissionError as e:
        raise HTTPException(401, str(e))


# ─────────────────────────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────────────────────────

class HRMChatRequest(BaseModel):
    query:      str
    session_id: Optional[str] = None


class HRMChatResponse(BaseModel):
    session_id: str
    answer:     str
    team:       str              = "HRM Team"
    agents:     list[str]        = ["Employee Agent", "Leave Info Agent"]
    sources:    list             = []
    metrics:    Optional[dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@hrm_router.post("/chat", response_model=HRMChatResponse, summary="Chat với HRM Team")
async def hrm_chat(
    req:  HRMChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Chat với **HRM Team** — team AI chuyên nhân sự HITC.

    Team gồm 2 agents chuyên biệt:
    - **Employee Agent**: tra cứu thông tin nhân viên, tìm kiếm, danh sách, thâm niên
    - **Leave Info Agent**: ngày nghỉ lễ, quy định nghỉ tuần, loại nghỉ phép

    **Ví dụ query:**
    ```
    "Thông tin nhân viên của tôi"
    "Tìm nhân viên tên Nguyễn Văn A"
    "Danh sách nhân viên phòng Kế toán"
    "Năm 2025 có những ngày nghỉ lễ nào?"
    "Công ty nghỉ mấy ngày trong tuần?"
    "Nghỉ phép được bao nhiêu ngày?"
    "Ngày 02/09/2025 có phải ngày làm việc không?"
    "Chính sách nghỉ phép của công ty là gì?"
    ```
    """
    try:
        sid     = req.session_id or str(uuid.uuid4())
        history = session_store.load(sid)

        result = await chat_with_hrm_team(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
        )

        return HRMChatResponse(
            session_id=result["session_id"],
            answer=str(result["answer"]),
            team=result.get("team", "HRM Team"),
            agents=result.get("agents", []),
            sources=result.get("sources", []),
            metrics=result.get("metrics"),
        )
    except Exception as e:
        logger.error("HRM chat error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@hrm_router.get("/holidays", summary="Danh sách ngày nghỉ lễ")
async def get_holidays_direct(
    year:         Optional[int] = Query(None, description="Năm (mặc định năm hiện tại)"),
    from_date:    Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date:      Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    company_code: str           = Query("HITC", description="Mã công ty"),
    user: UserPermissionContext = Depends(get_user),
):
    """
    Lấy danh sách ngày nghỉ lễ chính thức **trực tiếp** (không qua LLM).

    Nhanh hơn `/hrm/chat` cho query thuần dữ liệu.
    Hỗ trợ lọc theo năm hoặc khoảng thời gian cụ thể.
    """
    from app.db.mongo import get_db
    from datetime import datetime, timedelta

    db  = get_db()
    col = db["instance_data_ngay_nghi_le"]
    flt: dict = {"is_deleted": {"$ne": True}, "company_code": company_code}

    if from_date and to_date:
        try:
            dt_from = datetime.strptime(from_date, "%Y-%m-%d")
            dt_to   = datetime.strptime(to_date,   "%Y-%m-%d")
            flt["tu_ngay"]  = {"$lte": datetime(dt_to.year, dt_to.month, dt_to.day, 23, 59, 59)}
            flt["den_ngay"] = {"$gte": datetime(dt_from.year, dt_from.month, dt_from.day) - timedelta(hours=7)}
        except ValueError:
            raise HTTPException(400, "Định dạng ngày không hợp lệ. Dùng YYYY-MM-DD")
    else:
        target_year = year or datetime.now().year
        flt["$or"] = [
            {"tu_ngay":  {"$gte": datetime(target_year, 1, 1), "$lte": datetime(target_year, 12, 31, 23, 59, 59)}},
            {"den_ngay": {"$gte": datetime(target_year, 1, 1), "$lte": datetime(target_year, 12, 31, 23, 59, 59)}},
        ]

    from mcp_servers.hrm_server import _flatten_ngay_nghi_le
    docs       = list(col.find(flt).sort("tu_ngay", 1))
    items      = [_flatten_ngay_nghi_le(d) for d in docs]
    total_days = sum(d.get("so_ngay_nghi", 0) for d in docs if isinstance(d.get("so_ngay_nghi"), (int, float)))

    return {
        "year":           year or datetime.now().year,
        "total_holidays": len(items),
        "total_days_off": total_days,
        "summary":        f"Có {len(items)} đợt nghỉ lễ với tổng {total_days} ngày nghỉ",
        "holidays":       items,
    }


@hrm_router.get("/leave-types", summary="Danh mục loại nghỉ phép")
async def get_leave_types_direct(
    company_code: str = Query("HITC"),
    user: UserPermissionContext = Depends(get_user),
):
    """
    Lấy danh mục loại nghỉ phép **trực tiếp** (không qua LLM).

    Trả về tên loại nghỉ, số ngày tối đa/năm, có tính lương không.
    """
    from app.db.mongo import get_db
    from mcp_servers.hrm_server import _flatten_loai_nghi_phep

    db    = get_db()
    docs  = list(db["instance_data_danh_sach_loai_nghi_phep"].find(
        {"is_deleted": {"$ne": True}, "is_active": {"$ne": False}, "company_code": company_code},
    ).sort("ten_loai_nghi", 1))
    items = [_flatten_loai_nghi_phep(d) for d in docs]

    return {
        "total":       len(items),
        "summary":     f"Công ty có {len(items)} loại nghỉ phép",
        "leave_types": items,
    }


@hrm_router.get("/weekly-off-rules", summary="Quy định ngày nghỉ trong tuần")
async def get_weekly_off_rules_direct(
    company_code: str = Query("HITC"),
    user: UserPermissionContext = Depends(get_user),
):
    """
    Lấy quy định ngày nghỉ hàng tuần **trực tiếp** (không qua LLM).

    Trả về danh sách ngày nghỉ (Thứ 7, Chủ nhật...) và đơn vị áp dụng.
    """
    from app.db.mongo import get_db
    from mcp_servers.hrm_server import _flatten_ngay_nghi_tuan, _extract_value

    db   = get_db()
    docs = list(db["instance_data_ngay_nghi_tuan"].find(
        {"is_deleted": {"$ne": True}, "is_active": {"$ne": False}, "company_code": company_code},
    ).sort("muc_do_uu_tien", -1))

    items = [_flatten_ngay_nghi_tuan(d) for d in docs]
    seen: set = set()
    off_days: list[str] = []
    for d in docs:
        name = _extract_value(d.get("loai_nghi_tuan"))
        if name and str(name) not in seen:
            off_days.append(str(name))
            seen.add(str(name))

    return {
        "total":    len(items),
        "off_days": off_days,
        "summary":  f"Nghỉ hàng tuần vào: {', '.join(off_days)}" if off_days else "Chưa cấu hình",
        "detail":   items,
    }


@hrm_router.get("/session/{session_id}", summary="Lịch sử chat HRM")
async def get_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    """Lấy lịch sử hội thoại của session HRM."""
    messages = session_store.load(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@hrm_router.delete("/session/{session_id}", summary="Xoá lịch sử chat HRM")
async def clear_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    """Xoá lịch sử hội thoại của session HRM."""
    session_store.save(session_id, user.user_id, user.username, [])
    return {"session_id": session_id, "status": "cleared"}