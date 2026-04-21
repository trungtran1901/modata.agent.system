"""
app/api/routes/hitc_routes.py

HITC Unified API — single entry point cho toàn bộ HITC AgentOS.

Endpoints:
  POST /hitc/chat              — Chat tự động dispatch đến team phù hợp
  POST /hitc/chat/stream       — Chat SSE streaming
  POST /hitc/document/chat     — Chat với Document Intelligence Team (explicit)
  POST /hitc/document/stream   — Document Team SSE streaming
  GET  /hitc/teams             — Danh sách teams có sẵn
  GET  /hitc/session/{id}      — Lịch sử hội thoại
  DELETE /hitc/session/{id}    — Xoá lịch sử hội thoại

Design:
  - /hitc/chat: auto-detect team từ nội dung query
  - /hitc/document/chat: explicit Document Team, nhận thêm:
      document_content  — nội dung văn bản (có thể rất dài)
      output_schema     — schema JSON người dùng muốn (tuỳ chọn)
      role              — vai trò người dùng tự khai báo (tuỳ chọn)
      force_team        — ép buộc team cụ thể (tuỳ chọn)
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from utils.permission import PermissionService, UserPermissionContext
from workflow.hitc_agent import chat_with_hitc, stream_with_hitc
from workflow.session import session_store

logger      = logging.getLogger(__name__)
hitc_router = APIRouter(prefix="/hitc", tags=["HITC AgentOS"])
_perm_svc   = PermissionService()

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

class HitcChatRequest(BaseModel):
    """Request cho /hitc/chat — auto-detect team."""
    query:      str = Field(..., description="Câu hỏi hoặc yêu cầu của người dùng")
    session_id: Optional[str] = Field(None, description="Session ID (tự sinh nếu không truyền)")
    force_team: Optional[str] = Field(
        None,
        description="Ép buộc team: 'hrm' | 'document' (None = auto-detect từ query)",
    )


class DocumentChatRequest(BaseModel):
    """
    Request cho /hitc/document/chat — Document Intelligence Team.

    Người dùng có thể:
    1. Chỉ truyền query (nội dung văn bản inline trong query)
    2. Truyền document_content riêng + query là yêu cầu xử lý
    3. Truyền thêm output_schema để định nghĩa JSON output mong muốn
    4. Truyền role để agent điều chỉnh phong cách phản hồi

    Ví dụ sử dụng:
    - Tóm tắt văn bản:
        query="Tóm tắt văn bản này", document_content="... nội dung ..."
    - QA:
        query="Người ký văn bản này là ai?", document_content="..."
    - Trích xuất JSON:
        query="Trích xuất thông tin theo schema sau",
        document_content="...",
        output_schema='{"ten_nguoi_ky": null, "ngay_ky": null, "so_hieu": null}'
    - Trích xuất JSON không có schema (agent tự suy luận):
        query="Trích xuất tất cả thông tin quan trọng dưới dạng JSON",
        document_content="..."
    - QA với role:
        query="Điều khoản nào tôi cần chú ý?",
        document_content="...",
        role="Nhân viên mới ký hợp đồng lần đầu"
    """
    query: str = Field(
        ...,
        description="Yêu cầu xử lý: tóm tắt / câu hỏi / yêu cầu trích xuất JSON...",
    )
    session_id: Optional[str] = Field(
        None,
        description="Session ID (tự sinh nếu không truyền)",
    )
    document_content: Optional[str] = Field(
        None,
        description=(
            "Nội dung văn bản cần xử lý. "
            "Nếu không truyền, agent sẽ đọc từ nội dung query."
        ),
    )
    output_schema: Optional[str] = Field(
        None,
        description=(
            "Schema JSON mong muốn (JSON string). "
            "VD: '{\"ten\": null, \"ngay\": null, \"so_hieu\": null}' "
            "Nếu không truyền và query yêu cầu JSON → agent tự suy luận schema."
        ),
    )
    role: Optional[str] = Field(
        None,
        description=(
            "Vai trò người dùng (để agent điều chỉnh phản hồi). "
            "VD: 'HR manager', 'Nhân viên mới', 'Kế toán trưởng', 'Legal counsel'"
        ),
    )


class HitcChatResponse(BaseModel):
    session_id:  str
    answer:      str
    team:        str
    agents:      list[str]             = []
    team_used:   Optional[str]         = None
    sources:     list                  = []
    metrics:     Optional[dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@hitc_router.post(
    "/chat",
    response_model=HitcChatResponse,
    summary="HITC Chat — Auto-detect team",
)
async def hitc_chat(
    req:  HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    **HITC Unified Chat** — tự động phát hiện team phù hợp từ nội dung query.

    Routing logic:
    - Query liên quan đến nhân viên, chấm công, đơn từ, nghỉ phép → **HRM Team**
    - Query liên quan đến đọc văn bản, trích xuất JSON, QA tài liệu → **Document Team**
    - Có thể ép buộc team bằng `force_team`

    Xác thực:
    - `Authorization: Bearer <token>` (Keycloak JWT)
    - `X-Api-Key: <api_key>`
    """
    try:
        sid     = req.session_id or str(uuid.uuid4())
        history = session_store.load(sid)

        result = await chat_with_hitc(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
            force_team=req.force_team or "",
        )

        return HitcChatResponse(
            session_id=result["session_id"],
            answer=str(result.get("answer", "")),
            team=result.get("team", "HITC AgentOS"),
            agents=result.get("agents", []),
            team_used=result.get("team"),
            sources=result.get("sources", []),
            metrics=result.get("metrics"),
        )
    except Exception as e:
        logger.error("HITC chat error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@hitc_router.post(
    "/chat/stream",
    summary="HITC Chat — SSE streaming, auto-detect team",
)
async def hitc_chat_stream(
    req:  HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    **HITC Chat SSE Streaming** — phát hiện team tự động, trả về stream.

    Events:
    ```
    data: {"type": "token",  "content": "..."}
    data: {"type": "tool",   "name": "...", "status": "start"|"end"}
    data: {"type": "done",   "session_id": "...", "agent_id": "...", "team": "..."}
    data: {"type": "error",  "message": "..."}
    ```
    """
    sid     = req.session_id or str(uuid.uuid4())
    history = session_store.load(sid)

    return StreamingResponse(
        stream_with_hitc(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
            force_team=req.force_team or "",
        ),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )


@hitc_router.post(
    "/document/chat",
    response_model=HitcChatResponse,
    summary="Document Intelligence Team — đọc hiểu văn bản, QA, trích xuất JSON",
)
async def document_chat(
    req:  DocumentChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    **Document Intelligence Team** — xử lý văn bản linh hoạt theo yêu cầu người dùng.

    ---

    ### Các use case chính:

    **1. Tóm tắt văn bản:**
    ```json
    {
        "query": "Tóm tắt ngắn gọn nội dung văn bản này",
        "document_content": "... nội dung văn bản dài ..."
    }
    ```

    **2. Trả lời câu hỏi (QA):**
    ```json
    {
        "query": "Người ký văn bản này là ai? Ngày ký là bao giờ?",
        "document_content": "..."
    }
    ```

    **3. Trích xuất JSON theo schema:**
    ```json
    {
        "query": "Trích xuất thông tin theo schema JSON sau",
        "document_content": "...",
        "output_schema": "{\"ten_nguoi_ky\": null, \"ngay_ky\": null, \"so_hieu\": null, \"noi_dung_chinh\": null}"
    }
    ```

    **4. Trích xuất JSON tự do (agent tự suy luận schema):**
    ```json
    {
        "query": "Trích xuất tất cả thông tin quan trọng dưới dạng JSON",
        "document_content": "..."
    }
    ```

    **5. Điền form/template:**
    ```json
    {
        "query": "Điền vào form sau từ nội dung văn bản",
        "document_content": "...",
        "output_schema": "{\"ho_ten\": null, \"chuc_vu\": null, \"don_vi\": null, \"ngay_bat_dau\": null}"
    }
    ```

    **6. QA với context vai trò:**
    ```json
    {
        "query": "Tôi cần chú ý điều khoản nào?",
        "document_content": "... hợp đồng lao động ...",
        "role": "Nhân viên mới ký hợp đồng lần đầu"
    }
    ```

    **7. Làm giàu dữ liệu (tra cứu thêm từ hệ thống):**
    ```json
    {
        "query": "Trích xuất thông tin người ký và bổ sung thêm email, mã nhân viên từ hệ thống",
        "document_content": "..."
    }
    ```
    """
    try:
        sid     = req.session_id or str(uuid.uuid4())
        history = session_store.load(sid)

        result = await chat_with_hitc(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
            document_content=req.document_content or "",
            output_schema=req.output_schema or "",
            role=req.role or "",
            force_team="document",   # explicit Document Team
        )

        return HitcChatResponse(
            session_id=result["session_id"],
            answer=str(result.get("answer", "")),
            team=result.get("team", "Document Intelligence Team"),
            agents=result.get("agents", []),
            team_used=result.get("team"),
            sources=result.get("sources", []),
            metrics=result.get("metrics"),
        )
    except Exception as e:
        logger.error("Document chat error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@hitc_router.post(
    "/document/stream",
    summary="Document Intelligence Team — SSE streaming",
)
async def document_chat_stream(
    req:  DocumentChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """
    **Document Team SSE Streaming** — dùng cho văn bản dài, tránh timeout.

    Events giống `/hitc/chat/stream`.
    """
    sid     = req.session_id or str(uuid.uuid4())
    history = session_store.load(sid)

    return StreamingResponse(
        stream_with_hitc(
            query=req.query,
            user=user,
            session_id=sid,
            history=history,
            document_content=req.document_content or "",
            output_schema=req.output_schema or "",
            role=req.role or "",
            force_team="document",
        ),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
            "Connection":        "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────
# INFO ENDPOINTS
# ─────────────────────────────────────────────────────────────

@hitc_router.get("/teams", summary="Danh sách teams trong HITC AgentOS")
async def list_teams(
    user: UserPermissionContext = Depends(get_user),
):
    """Thông tin về các teams và agents có sẵn trong HITC AgentOS."""
    return {
        "agentos": "HITC AgentOS",
        "teams": [
            {
                "id":          "hrm-team",
                "name":        "HRM Team",
                "chat_url":    "POST /hrm/chat",
                "description": "Team nhân sự — nhân viên, chấm công, đơn từ, nghỉ phép",
                "agents": [
                    {"id": "hrm-employee-agent",   "name": "HRM Employee Agent",   "scope": "Thông tin nhân viên, tìm kiếm, thâm niên"},
                    {"id": "hrm-leave-agent",       "name": "HRM Leave Agent",      "scope": "Ngày nghỉ lễ, quy định nghỉ phép"},
                    {"id": "hrm-request-agent",     "name": "HRM Request Agent",    "scope": "Đơn xin nghỉ, đơn từ nhân sự"},
                    {"id": "hrm-attendance-agent",  "name": "HRM Attendance Agent", "scope": "Chấm công từng ngày, giờ vào/ra"},
                    {"id": "hrm-analytics-agent",   "name": "HRM Analytics Agent",  "scope": "Bảng tổng hợp chấm công, xuất Excel"},
                    {"id": "hrm-ocr-document-agent","name": "HRM OCR Document Agent","scope": "OCR tờ trình hành chính → JSON"},
                ],
            },
            {
                "id":          "document-team",
                "name":        "Document Intelligence Team",
                "chat_url":    "POST /hitc/document/chat",
                "description": "Team đọc hiểu văn bản — tóm tắt, QA, trích xuất JSON linh hoạt",
                "agents": [
                    {
                        "id":    "doc-reader-agent",
                        "name":  "Document Reader Agent",
                        "scope": "Tóm tắt văn bản, xác định loại, điểm quan trọng",
                    },
                    {
                        "id":    "doc-qa-agent",
                        "name":  "Document QA Agent",
                        "scope": "Trả lời câu hỏi cụ thể về nội dung văn bản",
                    },
                    {
                        "id":    "doc-extractor-agent",
                        "name":  "Document Extractor Agent",
                        "scope": "Trích xuất JSON theo schema người dùng định nghĩa",
                    },
                    {
                        "id":    "doc-enricher-agent",
                        "name":  "Document Enricher Agent",
                        "scope": "Làm giàu dữ liệu qua MCP (tra nhân viên, phòng ban)",
                    },
                ],
                "params": {
                    "document_content": "Nội dung văn bản cần xử lý",
                    "output_schema":    "Schema JSON mong muốn (tuỳ chọn)",
                    "role":             "Vai trò người dùng để agent điều chỉnh phản hồi (tuỳ chọn)",
                },
            },
        ],
        "unified_endpoint": {
            "chat":   "POST /hitc/chat   — auto-detect team từ query",
            "stream": "POST /hitc/chat/stream",
        },
        "ocr_pipeline": {
            "description": "OCR tờ trình hành chính 3 bước (pipeline cố định)",
            "url":         "POST /hrm/ocr",
            "stream_url":  "POST /hrm/ocr/stream",
        },
    }


@hitc_router.get("/session/{session_id}", summary="Lịch sử hội thoại")
async def get_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    messages = session_store.load(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@hitc_router.delete("/session/{session_id}", summary="Xoá lịch sử hội thoại")
async def clear_session(
    session_id: str,
    user: UserPermissionContext = Depends(get_user),
):
    session_store.save(session_id, user.user_id, user.username, [])
    return {"session_id": session_id, "status": "cleared"}