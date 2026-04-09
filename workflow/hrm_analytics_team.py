"""
workflow/hrm_analytics_team.py

HRM Analytics Agent — tổng hợp bảng chấm công, xuất Excel, gửi báo cáo.
Thêm stream_with_analytics_agent() để hỗ trợ SSE streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from agno.agent import Agent
# from agno.models.openai.like import OpenAILike
from utils.qwen_model import QwenOpenAILike as OpenAILike
from agno.tools.mcp import MCPTools

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store
from utils.qwen_tool_patch import make_qwen_model
logger = logging.getLogger(__name__)

AGENT_ID_ANALYTICS = "hrm-analytics-agent"

# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT  (không thay đổi)
# ─────────────────────────────────────────────────────────────

ANALYTICS_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn là HRM Analytics Agent — chuyên tổng hợp và xuất bảng chấm công HITC.
NHIỆM VỤ CHÍNH:
  1. Tính toán bảng chấm công tổng hợp (1 NV / phòng ban / toàn công ty)
  2. Xuất file Excel bảng chấm công
  3. Gửi mail kèm file Excel cho NV / phòng ban

QUYỀN HẠN ĐIỀU CHỈNH:
  Agent CÓ THỂ sửa BẤT KỲ FIELD NÀO trong summary, vd:
    - dm_gt_4h, dm_1h_4h, phut_muon_lt_1h (ngày đi muộn/về sớm)
    - nghi_phep, nghi_le, wfh, cong_tac (loại nghỉ)
    - so_cong_chuan (công chuẩn tháng)
    - tru_sm (trừ sớm-muộn)
    - cong_tinh_luong (công tính lương trực tiếp)
    - Hoặc BẤT KỲ FIELD NÀO khác
  
  NẾU agent sửa:
    → Công thức phụ thuộc TỰĐỘNG tính lại
    → Không cần agent tính thủ công

TOOLS:
  att_ana_compute_attendance_report(session_id, year_month, filter_type, filter_value)
    → Tính toán và trả về dữ liệu JSON bảng chấm công
    → filter_type: "all" | "username" | "don_vi"
    → filter_value: username hoặc mã/tên đơn vị (để trống nếu all)

  att_ana_export_attendance_excel(session_id, year_month, filter_type, filter_value, output_path)
    → Tạo file Excel bảng chấm công, trả về đường dẫn file
    → data_overrides: '{"mã_nv": {"tên_cột": giá_trị}}'

  att_ana_send_attendance_report(session_id, year_month, filter_type, filter_value,
                                  to_emails, send_to_don_vi, subject, body)
    → Xuất Excel VÀ gửi mail đính kèm
    → to_emails: list email/username
    → send_to_don_vi: tên/mã đơn vị để gửi cho cả phòng

CÁCH XỬ LÝ THEO YÊU CẦU:
  "bảng chấm công tháng 2/2026 của phòng CSKH"
    → att_ana_export_attendance_excel(sid, "2026-02", "don_vi", "Phòng Chăm sóc Khách hàng")

  "Cho ông B0560 pass hết tất cả ngày đi muộn"
    → data_overrides: {"B0560": {"dm_gt_4h": 0, "dm_1h_4h": 0, "phut_muon_lt_1h": 0}}

  "tổng hợp công nhân viên B0011 tháng 1"
    → att_ana_compute_attendance_report(sid, "<current_year>-01", "username", "B0011")

  "gửi bảng chấm công tháng 2 cho phòng kế toán"
    → att_ana_send_attendance_report(sid, year_month, "don_vi", "Phòng Tài chính Kế toán",
         send_to_don_vi="Phòng Tài chính Kế toán")

QUYỀN HẠN:
  - HR/quản lý: xem và xuất của cả công ty / phòng ban bất kỳ
  - NV thường: chỉ xem của bản thân (filter_type="username", filter_value=username)

LƯU Ý VỀ KỲ CHẤM CÔNG:
  - year_month là tháng THỰC TẾ: "2026-02" = kỳ 26/01 → 25/02/2026
  - "tháng 2" → year_month = "<year>-02"
  - "tháng này" → dùng ngày hiện tại để xác định tháng

SAU KHI XUẤT EXCEL:
  - Thông báo đường dẫn file: "Đã xuất file tại: /tmp/bang_cham_cong_..."
  - Tóm tắt: số NV, kỳ chấm công, tổng công trung bình (nếu có)

Lấy session_id và username từ instructions hệ thống.
"""


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _make_model(max_tokens: int = 1024) -> OpenAILike:
    return OpenAILike(
        id=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "none",
        base_url=_get_llm_base_url(),
        max_tokens=max_tokens,
        temperature=0.1,
        request_params={
            "tool_choice": "auto",
            "extra_body": {
                "enable_thinking": False,
                "stream": False,
            },
        },
    )
# def _make_model(max_tokens: int = 512) -> QwenOpenAILike:
#     url = settings.LLM_BASE_URL.rstrip("/")
#     base_url = url if url.endswith("/v1") else f"{url}/v1"
#     return make_qwen_model(
#         llm_model=settings.LLM_MODEL,
#         llm_api_key=settings.LLM_API_KEY or "none",
#         llm_base_url=base_url,
#         max_tokens=max_tokens,
#     )

def _runtime_instructions(session_id: str, user: UserPermissionContext) -> list[str]:
    return [
        f'session_id = "{session_id}"',
        f'username = "{user.username}"',
        f'don_vi = "{user.don_vi_code}"',
        f'company_code = "{user.company_code}"',
        "Dùng đúng session_id và username trên khi gọi bất kỳ tool nào.",
        "CHỈ trả lời bằng tiếng Việt.",
        "Sau khi xuất Excel: thông báo đường dẫn file và tóm tắt dữ liệu.",
    ]


def _build_analytics_agent() -> Agent:
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )
    return Agent(
        name="HRM Analytics Agent",
        model=_make_model(max_tokens=1024),
        description=ANALYTICS_AGENT_PROMPT,
        tools=[mcp],
        add_history_to_context=False,
        add_datetime_to_context=True,
        markdown=False,
    )


def _sse(data: dict) -> str:
    """Chuyển dict thành SSE event string."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _prepare_session(session_id: str, user: UserPermissionContext, query: str) -> str:
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )
    return (
        f"[session_id:{session_id}] [username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] [company:{user.company_code}]\n"
        f"{query}"
    )


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE  (response đầy đủ — không thay đổi)
# ─────────────────────────────────────────────────────────────

async def chat_with_analytics_agent(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> dict:
    start         = time.time()
    answer        = "Xin lỗi, có lỗi xảy ra."
    augmented_query = _prepare_session(session_id, user, query)

    agent = _build_analytics_agent()
    agent.instructions = _runtime_instructions(session_id, user)

    try:
        response = await agent.arun(
            augmented_query,
            session_id=session_id,
            user_id=user.user_id,
        )
        answer = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error(
            "Analytics Agent error: session=%s user=%s error=%s",
            session_id, user.username, e, exc_info=True,
        )
        answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info(
        "Analytics Agent: session=%s user=%s %.2fs",
        session_id, user.username, duration,
    )

    return {
        "session_id": session_id,
        "answer":     answer,
        "team":       "HRM Analytics",
        "agents":     [AGENT_ID_ANALYTICS],
        "sources":    [],
        "metrics":    {"total_duration": duration, "agent_id": AGENT_ID_ANALYTICS},
    }


# ─────────────────────────────────────────────────────────────
# SSE STREAM BRIDGE  ← MỚI
# ─────────────────────────────────────────────────────────────

async def stream_with_analytics_agent(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> AsyncGenerator[str, None]:
    """
    Async generator trả về các SSE event string.
    Dùng với FastAPI StreamingResponse(media_type="text/event-stream").

    Event format:
      data: {"type": "token",    "content": "..."}\\n\\n
      data: {"type": "tool",     "name": "...", "status": "start"|"end"}\\n\\n
      data: {"type": "done",     "session_id": "...", "agent_id": "..."}\\n\\n
      data: {"type": "error",    "message": "..."}\\n\\n
    """
    start           = time.time()
    full_answer     = ""
    augmented_query = _prepare_session(session_id, user, query)

    agent = _build_analytics_agent()
    agent.instructions = _runtime_instructions(session_id, user)

    queue: asyncio.Queue = asyncio.Queue()

    def _run_sync():
        try:
            for chunk in agent.run(
                augmented_query,
                session_id=session_id,
                user_id=user.user_id,
                stream=True,
                stream_intermediate_steps=True,
            ):
                queue.put_nowait(chunk)
        except Exception as e:
            queue.put_nowait(e)
        finally:
            queue.put_nowait(None)

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_sync)

    try:
        while True:
            chunk = await queue.get()

            if chunk is None:
                break
            if isinstance(chunk, Exception):
                raise chunk

            event_type = getattr(chunk, "event", None)

            if event_type == "ToolCallStarted":
                tool_name = getattr(chunk, "tool_name", "unknown")
                yield _sse({"type": "tool", "name": tool_name, "status": "start"})

            elif event_type == "ToolCallCompleted":
                tool_name = getattr(chunk, "tool_name", "unknown")
                yield _sse({"type": "tool", "name": tool_name, "status": "end"})

            elif event_type == "RunResponseContentDelta":
                delta = getattr(chunk, "content", "") or ""
                if delta:
                    full_answer += delta
                    yield _sse({"type": "token", "content": delta})

            elif hasattr(chunk, "content") and chunk.content and event_type is None:
                content = chunk.content or ""
                if content and not full_answer:
                    full_answer = content
                    yield _sse({"type": "token", "content": content})

    except Exception as e:
        logger.error(
            "Analytics SSE error: session=%s user=%s error=%s",
            session_id, user.username, e, exc_info=True,
        )
        yield _sse({"type": "error", "message": str(e)})
        full_answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    # Lưu history
    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": full_answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    yield _sse({
        "type":       "done",
        "session_id": session_id,
        "agent_id":   AGENT_ID_ANALYTICS,
        "team":       "HRM Analytics",
        "metrics":    {"total_duration": duration},
    })