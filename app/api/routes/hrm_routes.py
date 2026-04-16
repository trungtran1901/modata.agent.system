"""
app/api/routes/hrm_routes.py

HRM Team API + OCR Document API

Endpoints HRM:
  POST /hrm/chat               — Chat với HRM Team
  POST /hrm/chat/stream        — Chat SSE streaming
  GET  /hrm/holidays           — Ngày nghỉ lễ (không qua LLM)
  GET  /hrm/leave-types        — Loại nghỉ phép (không qua LLM)
  GET  /hrm/weekly-off-rules   — Quy định nghỉ tuần (không qua LLM)
  GET  /hrm/session/{id}       — Lịch sử chat HRM
  DELETE /hrm/session/{id}     — Xoá lịch sử chat HRM

Endpoints OCR:
  POST /hrm/ocr                — Xử lý văn bản OCR → JSON tờ trình
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, field_validator
from pydantic import ConfigDict

from utils.permission import PermissionService, UserPermissionContext
from workflow.hrm_team import chat_with_hrm_team, stream_with_hrm_team
from workflow.ocr_team import process_ocr_document, stream_ocr_document
from workflow.session import session_store

logger     = logging.getLogger(__name__)
hrm_router = APIRouter(prefix="/hrm", tags=["HRM Team"])
_perm_svc  = PermissionService()


# ─────────────────────────────────────────────────────────────
# AUTH DEPENDENCY
# ─────────────────────────────────────────────────────────────

async def get_user(
    authorization: Optional[str] = Header(None),
    x_api_key:     Optional[str] = Header(None, alias="X-Api-Key"),
) -> UserPermissionContext:
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Header Authorization không hợp lệ. Dùng: Bearer <token>")
        try:
            return await _perm_svc.build_context(authorization)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    if x_api_key:
        try:
            return _perm_svc.build_context_from_api_key(x_api_key)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    raise HTTPException(
        401,
        "Cần xác thực. Truyền 'Authorization: Bearer <token>' hoặc 'X-Api-Key: <api_key>'",
    )


# ─────────────────────────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────────────────────────

class HRMChatRequest(BaseModel):
    query:      str
    session_id: Optional[str] = None


class HRMChatResponse(BaseModel):
    session_id: str
    answer:     str
    team:       str                      = "HRM Team"
    agents:     list[str]                = []
    sources:    list                     = []
    metrics:    Optional[dict[str, Any]] = None


class OCRRequest(BaseModel):
    ocr_text:   str
    session_id: Optional[str] = None


class OCRResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session_id: str
    result:     Any           # JSON object đã parse
    raw:        str           # JSON string gốc
    pipeline:   str                      = "ocr-pipeline-3step"
    metrics:    Optional[dict[str, Any]] = None

    @field_validator("raw", mode="before")
    @classmethod
    def coerce_raw_to_str(cls, v: Any) -> str:
        """Đảm bảo raw luôn là string — tránh 422 khi process_ocr_document trả về None."""
        if v is None:
            return ""
        return str(v) if not isinstance(v, str) else v


# ─────────────────────────────────────────────────────────────
# HRM CHAT ENDPOINTS
# ─────────────────────────────────────────────────────────────

@hrm_router.post("/chat", response_model=HRMChatResponse, summary="Chat với HRM Team")
async def hrm_chat(
    req:  HRMChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Chat với **HRM Team** — team AI chuyên nhân sự HITC.

    Xác thực qua:
    - `Authorization: Bearer <token>` (Keycloak JWT)
    - `X-Api-Key: <api_key>` (API Key trong MongoDB)

    Agents:
    - **Employee Agent**: tra cứu thông tin nhân viên
    - **Leave Info Agent**: ngày nghỉ lễ, quy định nghỉ
    - **Request Agent**: tra cứu đơn từ nhân sự
    - **Attendance Agent**: giờ vào/ra từng ngày
    - **Analytics Agent**: bảng chấm công tổng hợp, xuất Excel
    """
    try:
        sid     = req.session_id or str(uuid.uuid4())
        history = session_store.load(sid)
        result  = await chat_with_hrm_team(
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


@hrm_router.post("/chat/stream", summary="Chat với HRM Team — SSE streaming")
async def hrm_chat_stream(
    req:  HRMChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Chat SSE streaming. Response `text/event-stream`:
    ```
    data: {"type": "token",  "content": "..."}
    data: {"type": "tool",   "name": "...", "status": "start"|"end"}
    data: {"type": "done",   "session_id": "...", "agent_id": "...", "metrics": {...}}
    data: {"type": "error",  "message": "..."}
    ```
    """
    sid     = req.session_id or str(uuid.uuid4())
    history = session_store.load(sid)
    return StreamingResponse(
        stream_with_hrm_team(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
        ),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────
# OCR ENDPOINT
# ─────────────────────────────────────────────────────────────

@hrm_router.post(
    "/ocr",
    summary="Xử lý văn bản OCR tờ trình → JSON chuẩn",
)
async def hrm_ocr(
    req:  OCRRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Nhận văn bản OCR của tờ trình hành chính, trả về JSON chuẩn gồm:
    - Thông tin tài liệu (tiêu đề, số hiệu, ngày lập...)
    - Người lập (tra cứu từ HRM qua MCP)
    - Lãnh đạo phê duyệt (tra cứu từ HRM qua MCP)
    - Phòng ban (tra cứu từ org tree qua MCP)

    Pipeline xử lý 3 bước tuần tự:
    1. Trích xuất text fields từ OCR
    2. Tra cứu nhân viên + phòng ban qua MCP tools
    3. Lắp ghép JSON output chuẩn

    **Request body:**
    ```json
    {
        "ocr_text": "--- Page 0 ---\\nHITC...\\n--- Page 1 ---\\n...",
        "session_id": "optional-uuid"
    }
    ```

    **Response:**
    ```json
    {
        "session_id": "...",
        "result": { "ten_tai_lieu": "...", "nguoi_lap_to_trinh": {...}, ... },
        "raw": "{...}",
        "pipeline": "ocr-pipeline-3step"
    }
    ```
    """
    try:
        sid = req.session_id or str(uuid.uuid4())
        t0  = time.time()

        raw_json = await process_ocr_document(
            ocr_text=req.ocr_text,
            session_id=sid,
            user=user,
        )

        # Đảm bảo raw_json luôn là str
        if raw_json is None:
            raw_json = "{}"
        elif not isinstance(raw_json, str):
            raw_json = json.dumps(raw_json, ensure_ascii=False)

        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("OCR: raw_json không parse được, fallback raw_text | session=%s", sid)
            parsed = {"raw_text": raw_json}

        duration = round(time.time() - t0, 3)
        logger.info("OCR endpoint done | session=%s %.2fs", sid, duration)

        return JSONResponse(content={
            "session_id": sid,
            "result":     parsed,
            "raw":        raw_json,
            "pipeline":   "ocr-pipeline-3step",
            "metrics":    {"total_duration": duration},
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error("OCR endpoint error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@hrm_router.post("/ocr/stream", summary="Xử lý OCR tờ trình — SSE streaming từng bước")
async def hrm_ocr_stream(
    req:  OCRRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Giống `/ocr` nhưng trả về **SSE stream** để tránh timeout trên pipeline dài.

    Client nhận liên tục các events trong khi pipeline chạy (~60-120s):

    ```
    event: progress
    data: {"step": 1, "total": 4, "message": "Đang trích xuất thông tin..."}

    event: result
    data: {"step": 1, "data": {"ten_nguoi_lap": "...", ...}}

    event: progress
    data: {"step": 2, "total": 4, "message": "Đang tra cứu nhân viên: ..."}

    event: result
    data: {"step": 2, "data": {"nguoi_lap_username": "...", ...}}

    event: progress
    data: {"step": 3, "total": 4, "message": "Đang tra cứu phòng ban: ..."}

    event: result
    data: {"step": 3, "data": {"phong_ban_code": "...", ...}}

    event: progress
    data: {"step": 4, "total": 4, "message": "Đang lắp ghép kết quả cuối..."}

    event: done
    data: {"session_id": "...", "result": {...}, "raw": "...",
           "pipeline": "ocr-pipeline-3step", "metrics": {"total_duration": 82.5}}

    event: error  (chỉ khi có lỗi, thay cho done)
    data: {"message": "...", "step": 2}
    ```

    **Ưu điểm so với `/ocr`:**
    - Không bao giờ timeout — keepalive qua `progress` events
    - Client biết tiến độ thực tế từng bước
    - Kết quả cuối cùng giống hệt `/ocr` (trong event `done`)
    """
    sid = req.session_id or str(uuid.uuid4())
    return StreamingResponse(
        stream_ocr_document(
            ocr_text=req.ocr_text,
            session_id=sid,
            user=user,
        ),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",   # tắt nginx buffering
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )



async def hrm_ocr_debug(
    request: Request,
    user: UserPermissionContext = Depends(get_user),
):
    """Endpoint tạm để debug 422 — log raw body và content-type."""
    body        = await request.body()
    content_type = request.headers.get("content-type", "")
    logger.info("OCR DEBUG | content-type=%s | body=%s", content_type, body[:500])
    return {
        "content_type": content_type,
        "raw_body":     body.decode(errors="replace"),
        "user":         user.username,
    }


# ─────────────────────────────────────────────────────────────
# DIRECT ENDPOINTS (không qua LLM)
# ─────────────────────────────────────────────────────────────

@hrm_router.get("/holidays", summary="Danh sách ngày nghỉ lễ")
async def get_holidays_direct(
    year:         Optional[int] = Query(None, description="Năm (mặc định năm hiện tại)"),
    from_date:    Optional[str] = Query(None, description="Từ ngày YYYY-MM-DD"),
    to_date:      Optional[str] = Query(None, description="Đến ngày YYYY-MM-DD"),
    company_code: str           = Query("HITC", description="Mã công ty"),
    user: UserPermissionContext = Depends(get_user),
):
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
    from app.db.mongo import get_db
    from mcp_servers.hrm_server import _flatten_loai_nghi_phep

    db    = get_db()
    docs  = list(db["instance_data_danh_sach_loai_nghi_phep"].find(
        {"is_deleted": {"$ne": True}, "is_active": {"$ne": False}, "company_code": company_code},
    ).sort("ten_loai_nghi", 1))
    items = [_flatten_loai_nghi_phep(d) for d in docs]
    return {"total": len(items), "summary": f"Công ty có {len(items)} loại nghỉ phép", "leave_types": items}


@hrm_router.get("/weekly-off-rules", summary="Quy định ngày nghỉ trong tuần")
async def get_weekly_off_rules_direct(
    company_code: str = Query("HITC"),
    user: UserPermissionContext = Depends(get_user),
):
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
    messages = session_store.load(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@hrm_router.delete("/session/{session_id}", summary="Xoá lịch sử chat HRM")
async def clear_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    session_store.save(session_id, user.user_id, user.username, [])
    return {"session_id": session_id, "status": "cleared"}