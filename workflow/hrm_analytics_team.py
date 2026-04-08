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
QUY TRÌNH TƯ DUY (THỰC HIỆN THEO THỨ TỰ):
1. XÁC ĐỊNH ĐỐI TƯỢNG: Nếu user nói tên người (ví dụ "ông Hải"), bạn KHÔNG ĐƯỢC đoán mã NV. Hãy gọi tool tra cứu nhân viên để lấy 'ma_nv' chính xác (ví dụ B0495).
2. TÙY BIẾN CỘT: Nếu user yêu cầu thêm thông tin không có trong bảng gốc (ví dụ "Thưởng dự án", "Xếp loại"), hãy điền tên cột và giá trị mặc định vào tham số `extra_columns`.
3. ĐIỀU CHỈNH SỐ LIỆU: Điền các giá trị cần sửa vào `data_overrides` theo đúng 'ma_nv'. 
   - Để "pass" đi muộn: Phải set cả 'tru_sm': 0, 'dm_gt_4h': 0, 'dm_1h_4h': 0, 'phut_muon_lt_1h': 0.

VÍ DỤ LỆNH: "Cho ông Vương Ngọc Hải 26 công, xóa mọi lỗi đi muộn và thêm cột 'Thưởng nóng' 1 triệu"
BƯỚC 1: Tìm "Vương Ngọc Hải" -> Trả về ma_nv "B0495".
BƯỚC 2: Gọi export_attendance_excel với:
  extra_columns='{"Thưởng nóng": 0}'
  data_overrides='{"B0495": {"cong_tinh_luong": 26, "tru_sm": 0, "dm_gt_4h": 0, "dm_1h_4h": 0, "phut_muon_lt_1h": 0, "Thưởng nóng": 1000000}}'
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