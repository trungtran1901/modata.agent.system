"""
workflow/hrm_analytics_team.py

HRM Analytics Agent — tổng hợp bảng chấm công, xuất Excel, gửi báo cáo.

Agent sử dụng các tools:
  att_ana_compute_attendance_report  — tính toán dữ liệu
  att_ana_export_attendance_excel    — xuất Excel
  att_ana_send_attendance_report     — gửi mail + Excel

Phối hợp với HRM Team qua hrm_team.py nếu cần tra cứu thêm.
"""
from __future__ import annotations

import logging
import time

from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.tools.mcp import MCPTools

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

AGENT_ID_ANALYTICS = "hrm-analytics-agent"

# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

ANALYTICS_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn là HRM Analytics Agent — chuyên tổng hợp và xuất bảng chấm công HITC.

NHIỆM VỤ CHÍNH:
  1. Tính toán bảng chấm công tổng hợp (1 NV / phòng ban / toàn công ty)
  2. Xuất file Excel bảng chấm công
  3. Gửi mail kèm file Excel cho NV / phòng ban

TOOLS:
  att_ana_compute_attendance_report(session_id, year_month, filter_type, filter_value)
    → Tính toán và trả về dữ liệu JSON bảng chấm công
    → filter_type: "all" | "username" | "don_vi"
    → filter_value: username hoặc mã/tên đơn vị (để trống nếu all)

  att_ana_export_attendance_excel(session_id, year_month, filter_type, filter_value, output_path)
    → Tạo file Excel bảng chấm công, trả về đường dẫn file

  att_ana_send_attendance_report(session_id, year_month, filter_type, filter_value,
                                  to_emails, send_to_don_vi, subject, body)
    → Xuất Excel VÀ gửi mail đính kèm
    → to_emails: list email/username
    → send_to_don_vi: tên/mã đơn vị để gửi cho cả phòng

CÁCH XỬ LÝ THEO YÊU CẦU:
  "bảng chấm công tháng 2/2026 của phòng CSKH"
    → att_ana_export_attendance_excel(sid, "2026-02", "don_vi", "Phòng Chăm sóc Khách hàng")
    → Thông báo đường dẫn file + tóm tắt kết quả

  "tổng hợp công nhân viên B0011 tháng 1"
    → att_ana_compute_attendance_report(sid, "<current_year>-01", "username", "B0011")
    → Trình bày kết quả dạng bảng

  "gửi bảng chấm công tháng 2 cho phòng kế toán"
    → att_ana_send_attendance_report(sid, year_month, "don_vi", "Phòng Tài chính Kế toán",
         send_to_don_vi="Phòng Tài chính Kế toán")
    → Xác nhận đã gửi

  "xuất bảng chấm công toàn công ty tháng này và gửi cho HR"
    → att_ana_send_attendance_report(sid, year_month, "all", "",
         to_emails=["hr@hitc.vn"])

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


def _is_analytics_query(query: str) -> bool:
    """Phát hiện query liên quan đến bảng chấm công / xuất Excel."""
    q = query.lower()
    keywords = [
        "bảng chấm công", "bang cham cong",
        "tổng hợp công", "tong hop cong",
        "xuất excel", "xuat excel", "export",
        "báo cáo công", "bao cao cong",
        "gửi bảng", "gui bang",
        "tính công", "tinh cong",
        "công tháng", "cong thang",
        "file excel", "download",
        "analytics", "thống kê chấm công",
    ]
    return any(kw in q for kw in keywords)


# ─────────────────────────────────────────────────────────────
# BUILDER
# ─────────────────────────────────────────────────────────────

def build_analytics_agent() -> Agent:
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse"
        # session_kwargs={"timeout": 60}
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


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE
# ─────────────────────────────────────────────────────────────

async def chat_with_analytics_agent(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> dict:
    """Entry point cho analytics queries từ HRM routes hoặc general chat."""
    start  = time.time()
    answer = "Xin lỗi, có lỗi xảy ra."

    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    augmented_query = (
        f"[session_id:{session_id}] [username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] [company:{user.company_code}]\n"
        f"{query}"
    )

    agent = build_analytics_agent()
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