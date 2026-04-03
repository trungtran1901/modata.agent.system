"""
workflow/hrm_team.py

HRM Teams — Multi-Agent Team chuyên biệt cho nhân sự HITC.

Kiến trúc (theo pattern của agents.py):
  ┌──────────────────────────────────────────────────────┐
  │                    HRM Team (Team)                   │
  │    mode="coordinate" — Leader phân công + tổng hợp  │
  ├──────────────────┬───────────────────────────────────┤
  │  Employee Agent  │        Leave Info Agent           │
  │  hrm_get_employee│  hrm_get_holidays                 │
  │  hrm_search_     │  hrm_get_weekly_off_rules         │
  │  hrm_list_       │  hrm_get_leave_types              │
  │  tools_calculate │  hrm_check_working_schedule       │
  │  _service_time   │  hrm_get_leave_policy_summary     │
  └──────────────────┴───────────────────────────────────┘
            ↓                         ↓
       MCP Gateway (modata-mcp :8001/sse)
            ↓                         ↓
       hrm_server.py tools      hrm_server.py tools
            ↓                         ↓
       MongoDB: thong_tin_nhan_vien   MongoDB: ngay_nghi_le
                                               ngay_nghi_tuan
                                               loai_nghi_phep

Luồng permission (giống agents.py):
  1. chat_with_hrm_team() → session_store.save_context()
     → Redis: perm:{session_id}:instances SET
     → PG:    rag_sessions.accessible_context JSON
  2. HRM Team gọi hrm_server tools với session_id
  3. hrm_server → get_session_context(session_id)
     → Redis SISMEMBER O(1) → can_access() → allow/deny

Context injection:
  Mỗi query được augment thêm:
  [session_id:xxx] [username:xxx] [don_vi:xxx] [company:xxx]
  → Agents đọc để truyền đúng session_id vào tools
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.team import Team
from agno.tools.mcp import MCPTools

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────

# ── Employee Agent ────────────────────────────────────────────
EMPLOYEE_AGENT_PROMPT = """\
Bạn là Employee Agent trong HRM Team của HITC.
Chuyên trách: tra cứu và trả lời về thông tin nhân viên.

COLLECTION CHÍNH: thong_tin_nhan_vien
TOOLS CHÍNH:
  hrm_get_employee_info(session_id, username_or_name)
    → Thông tin 1 nhân viên, tìm theo username hoặc họ tên
  hrm_search_employees(session_id, keyword, limit=10)
    → Tìm kiếm nhiều nhân viên theo tên/email/SĐT
  hrm_list_employees(session_id, don_vi_code=None, trang_thai="Đang làm việc", limit=20)
    → Danh sách nhân viên theo đơn vị / trạng thái

TOOLS HỖ TRỢ:
  tools_calculate_service_time(start_date, format="full")
    → Tính thâm niên công tác từ ngày_vao_lam lấy ở get_employee_info
  tools_get_current_time(format="date")
    → Lấy ngày hiện tại nếu cần

CÁC TÌNH HUỐNG VÀ CÁCH XỬ LÝ:
  ● "thông tin của tôi" / "hồ sơ của tôi"
    → hrm_get_employee_info(session_id, [username từ context])

  ● "thông tin nhân viên Nguyễn Văn A"
    → hrm_get_employee_info(session_id, "Nguyễn Văn A")

  ● "tìm nhân viên tên Hùng phòng IT"
    → hrm_search_employees(session_id, "Hùng")
    → Lọc kết quả theo phòng IT

  ● "danh sách nhân viên phòng Kế toán"
    → hrm_list_employees(session_id, don_vi_code="Kế toán")

  ● "có bao nhiêu nhân viên đang làm việc"
    → hrm_list_employees(session_id, limit=1) → xem total

  ● "Nguyễn Văn A đã làm bao lâu rồi?"
    1. hrm_get_employee_info(session_id, "Nguyễn Văn A")
       → lấy "Ngày vào làm"
    2. tools_calculate_service_time(start_date=<ngày_vao_lam>)
       → trả kết quả thâm niên

QUYỀN TRUY CẬP:
  - Collection thong_tin_nhan_vien cần quyền rõ ràng
  - Nếu nhận lỗi "không có quyền" → báo user và không tiếp tục

QUAN TRỌNG:
  - session_id lấy từ context: [session_id:xxx]
  - username lấy từ context: [username:xxx]
  - "của tôi" → dùng username từ context
  - Kết quả đã flatten: key = tiếng Việt, hiển thị trực tiếp

FORMAT TRẢ LỜI:
  - Ngắn gọn, rõ ràng, dùng bullet nếu nhiều thông tin
  - Có emoji phù hợp: 👤 nhân viên, 🏢 đơn vị, 💼 chức vụ, 📅 ngày tháng
  - Nếu tìm thấy nhiều kết quả: tóm tắt + liệt kê top N

Trả lời bằng tiếng Việt.
"""

# ── Leave Info Agent ──────────────────────────────────────────
LEAVE_INFO_AGENT_PROMPT = """\
Bạn là Leave Info Agent trong HRM Team của HITC.
Chuyên trách: tra cứu quy định nghỉ phép, ngày nghỉ lễ, chính sách nghỉ.

COLLECTIONS & TOOLS:
  1. Ngày nghỉ lễ — instance_data_ngay_nghi_le:
     hrm_get_holidays(session_id, year=<năm>, from_date=None, to_date=None)

  2. Quy định nghỉ tuần — instance_data_ngay_nghi_tuan:
     hrm_get_weekly_off_rules(session_id)

  3. Loại nghỉ phép — instance_data_danh_sach_loai_nghi_phep:
     hrm_get_leave_types(session_id)

  4. Kiểm tra ngày cụ thể:
     hrm_check_working_schedule(session_id, check_date="YYYY-MM-DD")

  5. Tổng hợp toàn bộ chính sách (1 lần gọi):
     hrm_get_leave_policy_summary(session_id)

  6. Lấy ngày hiện tại nếu cần:
     tools_get_current_time(format="date")

CÁC TÌNH HUỐNG VÀ CÁCH XỬ LÝ:
  ● "năm nay có những ngày nghỉ lễ nào?"
    → hrm_get_holidays(session_id, year=<năm hiện tại>)

  ● "tháng 4 nghỉ những ngày nào?" / "30/4 nghỉ mấy ngày?"
    → hrm_get_holidays(session_id, from_date="YYYY-04-01", to_date="YYYY-04-30")

  ● "công ty nghỉ mấy ngày trong tuần?"
    → hrm_get_weekly_off_rules(session_id)

  ● "nghỉ phép được bao nhiêu ngày?" / "có những loại nghỉ nào?"
    → hrm_get_leave_types(session_id)

  ● "ngày X/X/XXXX có phải ngày làm việc không?"
    → hrm_check_working_schedule(session_id, check_date="YYYY-MM-DD")

  ● "chính sách nghỉ phép của công ty" / "chế độ nghỉ là gì?"
    → hrm_get_leave_policy_summary(session_id)
    (1 lần gọi = đủ tất cả thông tin, không cần gọi 3 tools riêng)

QUAN TRỌNG:
  - session_id lấy từ context: [session_id:xxx]
  - Ngày tháng: convert sang định dạng dễ đọc DD/MM/YYYY khi trả lời
  - Ngày nghỉ lễ đã được xử lý múi giờ UTC→ICT, hiển thị đúng ngày VN
  - Khi không biết năm cụ thể → dùng tools_get_current_time() lấy năm hiện tại

FORMAT TRẢ LỜI:
  - 📅 ngày tháng cụ thể, 🏖️ nghỉ lễ, 📋 quy định
  - Liệt kê rõ từng ngày nghỉ lễ (tên + từ ngày → đến ngày + số ngày)
  - Tổng hợp cuối: "Tổng cộng X ngày nghỉ lễ trong năm YYYY"
  - Bảng nếu nhiều loại nghỉ phép

Trả lời bằng tiếng Việt.
"""

# ── HRM Team Leader ───────────────────────────────────────────
HRM_TEAM_LEADER_PROMPT = """\
Bạn là HRM Team Leader của HITC.
Nhiệm vụ: điều phối 2 agent chuyên biệt, tổng hợp và trả lời câu hỏi nhân sự.

TEAM MEMBERS:
  - Employee Agent: thông tin nhân viên, tìm kiếm, danh sách, thâm niên
  - Leave Info Agent: ngày nghỉ lễ, quy định nghỉ tuần, loại nghỉ phép

PHÂN CÔNG QUERY:
  → Employee Agent:
    nhân viên, hồ sơ, thông tin cá nhân, danh sách NV, phòng ban,
    chức vụ, lương, thâm niên công tác, tìm người

  → Leave Info Agent:
    nghỉ lễ, ngày lễ, lịch nghỉ, nghỉ phép, loại nghỉ, quy định nghỉ,
    ngày nghỉ tuần, chính sách, chế độ nghỉ, ngày làm việc

  → Cả hai (query kết hợp):
    "nhân viên A nghỉ phép được bao nhiêu ngày?"
    → Employee Agent lấy thông tin NV, Leave Info Agent lấy loại nghỉ

RULES:
  - Luôn trích xuất session_id và username từ context [session_id:xxx] [username:xxx]
  - Không tự bịa số liệu, không đoán mò khi không có dữ liệu
  - Tổng hợp kết quả từ agents thành câu trả lời mạch lạc, tự nhiên
  - Khi query đơn giản → phân công 1 agent (không gọi cả 2)
  - Khi query kết hợp → phân công song song nếu có thể

Trả lời ngắn gọn, chuyên nghiệp bằng tiếng Việt.
"""


# ─────────────────────────────────────────────────────────────
# MODEL FACTORY — giống pattern trong agents.py
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _make_model(max_tokens: int = 1024, temperature: float = 0.5) -> OpenAILike:
    """Tạo LLM instance — dùng cùng LLM server với agents.py."""
    return OpenAILike(
        id=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "none",
        base_url=_get_llm_base_url(),
        max_tokens=max_tokens,
        temperature=temperature,
        request_params={
            "tool_choice": "auto",
            "extra_body": {
                "enable_thinking": settings.LLM_ENABLE_THINKING,
                "stream": False,
            },
        },
    )


# ─────────────────────────────────────────────────────────────
# AGENT BUILDERS
# ─────────────────────────────────────────────────────────────

def _build_employee_agent(mcp_tools: MCPTools) -> Agent:
    """
    Employee Agent — chuyên truy xuất thông tin nhân viên.

    Tools cần thiết từ MCP Gateway (đã mount với prefix):
      hrm_get_employee_info, hrm_search_employees, hrm_list_employees
      tools_calculate_service_time, tools_get_current_time
    """
    return Agent(
        id="hrm-employee-agent",
        name="Employee Agent",
        role="Chuyên gia tra cứu thông tin nhân viên HITC",
        model=_make_model(max_tokens=1024, temperature=0.3),
        description=EMPLOYEE_AGENT_PROMPT,
        tools=[mcp_tools],
        add_datetime_to_context=True,
        markdown=False,
    )


def _build_leave_info_agent(mcp_tools: MCPTools) -> Agent:
    """
    Leave Info Agent — chuyên về quy định nghỉ phép, ngày nghỉ lễ.

    Tools cần thiết từ MCP Gateway (đã mount với prefix):
      hrm_get_holidays, hrm_get_weekly_off_rules, hrm_get_leave_types
      hrm_check_working_schedule, hrm_get_leave_policy_summary
      tools_get_current_time
    """
    return Agent(
        id="hrm-leave-info-agent",
        name="Leave Info Agent",
        role="Chuyên gia quy định nghỉ phép và ngày nghỉ lễ HITC",
        model=_make_model(max_tokens=1024, temperature=0.3),
        description=LEAVE_INFO_AGENT_PROMPT,
        tools=[mcp_tools],
        add_datetime_to_context=True,
        markdown=False,
    )


# ─────────────────────────────────────────────────────────────
# HRM TEAM BUILDER
# ─────────────────────────────────────────────────────────────

def build_hrm_team() -> Team:
    """
    Tạo HRM Team với 2 specialized agents.

    MCPTools được khởi tạo 1 lần và chia sẻ cho cả 2 agents.
    Agno Team mode="coordinate": Leader phân tích → phân công → tổng hợp.

    Cách thêm agent mới vào team:
      1. Thêm tools vào hrm_server.py
      2. Tạo hàm _build_xxx_agent() theo pattern dưới
      3. Thêm agent vào members=[] trong Team

    Returns:
        Team: Agno Team object, gọi .arun(query) để thực thi
    """
    # MCPTools kết nối modata-mcp gateway — 1 instance dùng chung
    mcp_tools = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )

    employee_agent  = _build_employee_agent(mcp_tools)
    leave_agent     = _build_leave_info_agent(mcp_tools)

    return Team(
        name="HRM Team",
        mode="coordinate",                 # Leader Agent điều phối
        model=_make_model(max_tokens=2048, temperature=0.4),
        members=[employee_agent, leave_agent],
        description=HRM_TEAM_LEADER_PROMPT,
        instructions=[
            "Trích xuất session_id và username từ [session_id:xxx] [username:xxx] trong query.",
            "Phân tích câu hỏi rõ ràng trước khi phân công agent.",
            "Query đơn → 1 agent. Query kết hợp → cả 2 agents.",
            "Tổng hợp kết quả thành câu trả lời tự nhiên, mạch lạc.",
            "Trả lời bằng tiếng Việt.",
        ],
        add_datetime_to_context=True,
        markdown=False,
        show_members_responses=False,      # Chỉ trả kết quả tổng hợp cuối cùng
        enable_agentic_state=True,       # Leader chia sẻ context với members
    )


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE — entry point từ /hrm/chat route
# ─────────────────────────────────────────────────────────────

async def chat_with_hrm_team(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> dict:
    """
    Entry point từ /hrm/chat endpoint → HRM Team.

    Giống pattern chat_with_agentosagno() trong agents.py:
      1. save_context()  → Redis/PG (hrm_server dùng session_id này để check quyền)
      2. augmented_query → inject [session_id] [username] cho agents
      3. Team.arun()     → Leader → phân công → agents gọi hrm_* tools → tổng hợp
      4. session_store.save() → lưu lịch sử hội thoại vào PG

    Args:
        query:      Câu hỏi của user (raw, chưa augment)
        user:       UserPermissionContext từ JWT verify + MongoDB RBAC
        session_id: UUID của session hiện tại
        history:    Lịch sử hội thoại từ PG (list[{role, content}])

    Returns:
        dict: {session_id, answer, team, agents, sources, metrics}
    """
    start = time.time()

    # ── 1. Lưu permission context vào Redis + PG ───────────────
    # modata-mcp (hrm_server) đọc session_id này để kiểm tra quyền
    # Phải gọi TRƯỚC khi Team.arun() vì agents sẽ ngay lập tức gọi tools
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,  # dict {instance: [ma_chuc_nang]}
        company_code=user.company_code,
    )

    # ── 2. Augment query với user context ─────────────────────
    # Agents đọc [session_id:xxx] để truyền vào hrm_* tool calls
    # Agents đọc [username:xxx] để xử lý "của tôi" → filter đúng user
    context_header = (
        f"[session_id:{session_id}] "
        f"[username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] "
        f"[company:{user.company_code}]"
    )

    # Inject lịch sử ngắn (max 3 turns = 6 messages, mỗi message max 200 ký tự)
    history_text = ""
    if history:
        recent = history[-(3 * 2):]
        lines  = []
        for m in recent:
            role    = "User" if m["role"] == "user" else "AI"
            content = m["content"][:200]
            if len(m["content"]) > 200:
                content += "…"
            lines.append(f"{role}: {content}")
        if lines:
            history_text = "\n[Lịch sử gần đây]\n" + "\n".join(lines) + "\n"

    augmented_query = f"{context_header}{history_text}\n\n[Câu hỏi]\n{query}"

    # ── 3. Khởi tạo và chạy HRM Team ─────────────────────────
    try:
        team     = build_hrm_team()
        response = await team.arun(
            augmented_query,
            session_id=session_id,
            user_id=user.user_id,
        )
        answer = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error("HRM Team error: session=%s user=%s error=%s",
                     session_id, user.username, e, exc_info=True)
        answer = f"Xin lỗi, có lỗi xảy ra khi xử lý yêu cầu: {str(e)}"

    # ── 4. Lưu lịch sử hội thoại vào PG ──────────────────────
    updated = history + [
        {"role": "user",      "content": query},   # Lưu query gốc, không augmented
        {"role": "assistant", "content": answer},
    ]
    updated = updated[-40:]                          # Giữ tối đa 20 turns trong DB
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info(
        "HRM Team done: session=%s user=%s %.2fs",
        session_id, user.username, duration,
    )

    return {
        "session_id": session_id,
        "answer":     answer,
        "team":       "HRM Team",
        "agents":     ["Employee Agent", "Leave Info Agent"],
        "sources":    [],
        "metrics":    {"total_duration": duration, "team": "hrm"},
    }