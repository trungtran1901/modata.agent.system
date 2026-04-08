"""
workflow/hrm_team.py  (v15 — tích hợp Analytics Agent)

Thêm AGENT_ID_ANALYTICS để xử lý các query:
  - bảng chấm công tổng hợp
  - xuất Excel
  - gửi báo cáo chấm công
  - tính toán công tháng theo phòng ban / toàn công ty
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
from workflow.hrm_analytics_team import chat_with_analytics_agent
logger = logging.getLogger(__name__)

# Agent IDs
AGENT_ID_EMPLOYEE   = "hrm-employee-agent"
AGENT_ID_LEAVE      = "hrm-leave-agent"
AGENT_ID_REQUEST    = "hrm-request-agent"
AGENT_ID_ATTENDANCE = "hrm-attendance-agent"
AGENT_ID_ANALYTICS  = "hrm-analytics-agent"   # ← MỚI


# ─────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────

EMPLOYEE_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn tra cứu thông tin nhân viên HITC.

TOOLS:
- hrm_get_employee_info(session_id, username_or_name)
- hrm_search_employees(session_id, keyword)
- hrm_list_employees(session_id, don_vi_code, trang_thai)
- tools_calculate_service_time(start_date)

Lấy session_id và username từ instructions hệ thống.
"của tôi" → dùng username từ instructions.
"""

LEAVE_INFO_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn tra cứu quy định nghỉ phép và ngày nghỉ lễ HITC.

TOOLS:
- hrm_get_holidays(session_id, year)
- hrm_get_weekly_off_rules(session_id)
- hrm_get_leave_types(session_id)
- hrm_check_working_schedule(session_id, check_date)
- hrm_get_leave_policy_summary(session_id)
- tools_get_current_time(format)

Lấy session_id từ instructions hệ thống. Ngày truyền vào: YYYY-MM-DD.
"""

REQUEST_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn tra cứu đơn từ nhân sự HITC.

LOẠI ĐƠN: "Nghỉ phép" | "Nghỉ ốm" | "Đi muộn, về sớm" | "Làm việc từ xa" | "Đề nghị đi công tác"
TRẠNG THÁI: "Đã duyệt" | "Chờ phê duyệt" | "Từ chối"

TOOLS:
- hrm_req_get_my_requests(session_id, username, loai_don, trang_thai, from_date, to_date, limit)
- hrm_req_list_requests(session_id, username, loai_don, trang_thai, don_vi_code, from_date, to_date)
- hrm_req_get_requests_by_user(session_id, target_username, loai_don, trang_thai)
- hrm_req_get_pending_requests(session_id, username, loai_don)
- hrm_req_get_request_stats(session_id, username, year, month)

Lấy session_id và username từ instructions hệ thống.
"""

ATTENDANCE_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn tra cứu dữ liệu chấm công thô HITC (giờ vào/ra từng ngày).

TOOLS:
- hrm_att_get_attendance_today(session_id, username)
- hrm_att_get_attendance_by_date(session_id, username, date)
- hrm_att_get_attendance_by_month(session_id, username, year_month)
- hrm_att_get_attendance_summary(session_id, username, year_month)
- hrm_att_get_attendance_range(session_id, username, from_date, to_date)

Lấy session_id và username từ instructions hệ thống.
"""

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
    → data_overrides: ĐÂY LÀ THAM SỐ QUAN TRỌNG ĐỂ ĐIỀU CHỈNH SỐ LIỆU. Truyền vào một chuỗi JSON nếu user yêu cầu sửa đổi/ép số liệu cụ thể cho ai đó. Định dạng: '{"mã_nv_hoặc_username": {"tên_cột_summary": giá_trị_mới}}'. 
      Các tên cột summary gồm: "cong_tinh_luong", "tong_cong_thuc_te", "nghi_phep", "tru_sm", v.v...

  att_ana_send_attendance_report(session_id, year_month, filter_type, filter_value,
                                  to_emails, send_to_don_vi, subject, body)
    → Xuất Excel VÀ gửi mail đính kèm, Hỗ trợ đè số liệu qua data_overrides như trên.
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
  - BÌNH THƯỜNG: "xuất bảng chấm công tháng 2/2026 của phòng CSKH"
    → att_ana_export_attendance_excel(..., filter_type="don_vi", filter_value="Phòng Chăm sóc Khách hàng")

  - CÓ ĐIỀU CHỈNH SỐ: "Xuất bảng chấm công phòng IT, nhưng cho nhân viên B0011 mặc định 26 công tính lương và nhân viên B0012 có 2 ngày nghỉ phép"
    → att_ana_export_attendance_excel(
          ..., 
          filter_type="don_vi", filter_value="Phòng IT", 
          custom_formula_notes="Đã điều chỉnh công theo yêu cầu: B0011 (26 công), B0012 (2 phép)",
          data_overrides='{"B0011": {"cong_tinh_luong": 26}, "B0012": {"nghi_phep": 2}}'
      )
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


def _make_model(max_tokens: int = 512) -> OpenAILike:
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
    ]


# ─────────────────────────────────────────────────────────────
# ROUTING
# ─────────────────────────────────────────────────────────────

def _decide_hrm_agent(query: str) -> str:
    q = query.lower()

    # Analytics Agent — ưu tiên cao nhất vì overlap với Attendance
    if any(kw in q for kw in [
        "bảng chấm công", "bang cham cong",
        "tổng hợp công", "tong hop cong",
        "xuất excel", "xuat excel", "export", "file excel",
        "báo cáo công", "bao cao cong",
        "gửi bảng", "gui bang", "gửi báo cáo",
        "tính công tháng", "tinh cong thang",
        "công tháng của phòng", "download",
        "tổng kết công", "tong ket cong",
        "bảng cc", "bang cc",
    ]):
        return AGENT_ID_ANALYTICS

    # Attendance Agent — giờ vào/ra từng ngày
    if any(kw in q for kw in [
        "chấm công", "check in", "check-in", "checkin",
        "check out", "check-out", "checkout",
        "giờ vào", "giờ ra", "vào lúc", "ra lúc",
        "giờ làm", "kỳ công", "chốt công",
        "ngày công", "tổng giờ", "bao nhiêu giờ",
        "hôm nay vào", "hôm nay ra",
    ]):
        return AGENT_ID_ATTENDANCE

    # Request Agent
    if any(kw in q for kw in [
        "đơn", "xin nghỉ", "nghỉ phép", "nghỉ ốm",
        "đi muộn", "về sớm", "remote", "làm việc từ xa",
        "công tác", "chờ duyệt", "đã duyệt", "từ chối",
        "nộp đơn", "trạng thái đơn", "thống kê đơn",
    ]):
        return AGENT_ID_REQUEST

    # Leave Info Agent
    if any(kw in q for kw in [
        "ngày lễ", "nghỉ lễ", "lịch nghỉ", "ngày nghỉ",
        "quy định nghỉ", "loại nghỉ", "phép năm",
        "chính sách nghỉ", "ngày làm việc",
        "thứ 7", "chủ nhật", "cuối tuần",
    ]):
        return AGENT_ID_LEAVE

    return AGENT_ID_EMPLOYEE


# ─────────────────────────────────────────────────────────────
# AGENT BUILDER
# ─────────────────────────────────────────────────────────────

def _build_hrm_agents() -> dict[str, Agent]:
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )

    common = dict(
        tools=[mcp],
        add_history_to_context=False,
        markdown=False,
    )

    return {
        AGENT_ID_EMPLOYEE: Agent(
            name="HRM Employee Agent",
            model=_make_model(max_tokens=512),
            description=EMPLOYEE_AGENT_PROMPT,
            add_datetime_to_context=False,
            **common,
        ),
        AGENT_ID_LEAVE: Agent(
            name="HRM Leave Agent",
            model=_make_model(max_tokens=512),
            description=LEAVE_INFO_AGENT_PROMPT,
            add_datetime_to_context=True,
            **common,
        ),
        AGENT_ID_REQUEST: Agent(
            name="HRM Request Agent",
            model=_make_model(max_tokens=512),
            description=REQUEST_AGENT_PROMPT,
            add_datetime_to_context=True,
            **common,
        ),
        AGENT_ID_ATTENDANCE: Agent(
            name="HRM Attendance Agent",
            model=_make_model(max_tokens=512),
            description=ATTENDANCE_AGENT_PROMPT,
            add_datetime_to_context=True,
            **common,
        ),
        AGENT_ID_ANALYTICS: Agent(
            name="HRM Analytics Agent",
            model=_make_model(max_tokens=1024),
            description=ANALYTICS_AGENT_PROMPT,
            add_datetime_to_context=True,
            **common,
        ),
    }


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE
# ─────────────────────────────────────────────────────────────

async def chat_with_hrm_team(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> dict:
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

    agent_id = _decide_hrm_agent(query)
    logger.info("HRM routing → %s | query=%s", agent_id, query[:60])
    if agent_id == AGENT_ID_ANALYTICS:
        logger.info("Bypass local agent, calling chat_with_analytics_agent...")
        return await chat_with_analytics_agent(query, user, session_id, history)
    agents = _build_hrm_agents()
    agent  = agents[agent_id]
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
            "HRM Agent error: agent=%s session=%s user=%s error=%s",
            agent_id, session_id, user.username, e, exc_info=True,
        )
        answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info(
        "HRM Agent: agent=%s session=%s user=%s %.2fs",
        agent_id, session_id, user.username, duration,
    )

    return {
        "session_id": session_id,
        "answer":     answer,
        "team":       "HRM Team",
        "agents":     [agent_id],
        "sources":    [],
        "metrics":    {"total_duration": duration, "agent_id": agent_id},
    }