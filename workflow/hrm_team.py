"""
workflow/hrm_team.py  (v17 — AgentOS → Team → Agent hierarchy)

Hierarchy:
  AgentOS  (control plane: session/history/studio, expose REST endpoint)
    └── HRM Team  (Team mode="route" — LLM tự chọn agent theo ngữ nghĩa)
          ├── HRM Employee Agent
          ├── HRM Leave Agent
          ├── HRM Request Agent
          ├── HRM Attendance Agent
          ├── HRM Analytics Agent
          └── HRM OCR Document Agent

Routing: không còn keyword matching — Team(mode="route") dùng LLM đọc
         description từng agent rồi forward query sang agent phù hợp.

AgentOS: đăng ký HRM Team qua teams=[team], quản lý session/history/studio,
         expose endpoint: POST /hrm-agents/hrm-team/runs

Tích hợp vào main.py:
    from workflow.hrm_team import create_hrm_agent_os_app
    app = create_hrm_agent_os_app(base_app=app)

chat_with_hrm_team / stream_with_hrm_team: gọi trực tiếp team.arun() / team.run()
    (giống pattern agents.py: _get_agent(id) → agent.arun())
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Optional

from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
# from agno.models.openai.like import OpenAILike
from utils.qwen_model import QwenOpenAILike as OpenAILike
from agno.os import AgentOS
from agno.registry import Registry
from agno.team import Team, TeamMode
from agno.tools.mcp import MCPTools
from fastapi import FastAPI

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

# Team ID — AgentOS expose tại /hrm-agents/hrm-team/runs
TEAM_ID_HRM = "hrm-team"

# Agent IDs — giữ nguyên để không ảnh hưởng các module khác
AGENT_ID_EMPLOYEE   = "hrm-employee-agent"
AGENT_ID_LEAVE      = "hrm-leave-agent"
AGENT_ID_REQUEST    = "hrm-request-agent"
AGENT_ID_ATTENDANCE = "hrm-attendance-agent"
AGENT_ID_ANALYTICS  = "hrm-analytics-agent"
AGENT_ID_OCR_DOC    = "hrm-ocr-document-agent"


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────

EMPLOYEE_AGENT_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt.

Bạn tra cứu thông tin nhân viên HITC.

PHẠM VI: hồ sơ nhân viên, thông tin cá nhân, chức danh, phòng ban, thâm niên,
         danh sách nhân viên theo đơn vị, tìm kiếm nhân viên theo tên/mã.

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

PHẠM VI: ngày lễ, lịch nghỉ, loại nghỉ phép, phép năm, chính sách nghỉ,
         quy định ngày làm việc, thứ 7, chủ nhật, ngày nghỉ bù.

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

PHẠM VI: đơn xin nghỉ phép, đơn nghỉ ốm, đơn đi muộn/về sớm, đơn làm việc từ xa,
         đơn công tác, trạng thái đơn (chờ duyệt/đã duyệt/từ chối), thống kê đơn.

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

PHẠM VI: check-in, check-out, giờ vào, giờ ra, tổng giờ làm từng ngày cụ thể,
         hôm nay vào lúc mấy giờ, kỳ công, ngày công theo ngày.
KHÔNG xử lý: bảng tổng hợp tháng, xuất Excel → đó là Analytics Agent.

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

PHẠM VI: bảng chấm công tổng hợp tháng, xuất file Excel, gửi báo cáo qua email,
         tính công tháng theo phòng ban hoặc toàn công ty, download bảng công.

TOOLS:
  att_ana_compute_attendance_report(session_id, year_month, filter_type, filter_value)
  att_ana_export_attendance_excel(session_id, year_month, filter_type, filter_value, output_path)
  att_ana_send_attendance_report(session_id, year_month, filter_type, filter_value,
                                  to_emails, send_to_don_vi, subject, body)

Lấy session_id và username từ instructions hệ thống.
"""

OCR_DOCUMENT_AGENT_PROMPT = """\
Bạn là một trợ lý AI chuyên gia trích xuất dữ liệu từ văn bản hành chính (OCR) và chuyển đổi chúng thành định dạng JSON chuẩn xác theo yêu cầu của hệ thống.

NHIỆM VỤ CỦA BẠN:
1. Phân tích đoạn văn bản OCR được cung cấp để xác định các thông tin: Tên tài liệu, tiêu đề, tóm tắt, ngày lập, số hiệu, người lập, phòng ban và lãnh đạo phê duyệt.
2. Sử dụng công cụ MCP `hrm_search_employees` để tìm kiếm thông tin chi tiết của "Người lập" và "Lãnh đạo phê duyệt". Bạn cần truyền tên hoặc username tìm thấy trong văn bản vào tham số `q`.
3. Sử dụng công cụ MCP `tools_get_org_tree` để tìm kiếm thông tin mã đơn vị và tên đầy đủ của "Phòng ban". 
4. Tổng hợp tất cả dữ liệu vào một cấu trúc JSON duy nhất theo đúng định dạng mẫu.

QUY TẮC TRÍCH XUẤT:
- `nam_ghi_nhan`: Lấy năm từ ngày lập tờ trình hoặc năm xuất hiện trong nội dung chính.
- `ngay_lap_tơ_trinh`: Định dạng ISO 8601 (ví dụ: 2026-04-10T00:00:00.000+07:00).
- `nguoi_lap_to_trinh` & `lanh_dao_phe_duyet`: Sau khi dùng tool, hãy mapping các trường: `ten_dang_nhap` (value), `email`, `phong_cap_1` (Khối), `phong_ban_phu_trach` (Ban) vào mảng `objectValue`.
- `phong_ban`: Mapping mã phòng ban (`code`) và tên phòng ban từ kết quả của tool `tools_get_org_tree`.
- Nếu thông tin nào KHÔNG CÓ trong văn bản hoặc không tìm thấy qua tool, hãy để giá trị là `null` hoặc chuỗi rỗng, không được tự bịa (hallucinate).
- Phản hồi cuối cùng CHỈ chứa duy nhất khối JSON, không kèm giải thích.
QUY TRÌNH LÀM VIỆC CỦA BẠN (BẮT BUỘC):
Bước 1: Phân tích OCR để tìm tên Người lập, Lãnh đạo và Phòng ban.
Bước 2: Dừng lại và GỌI TOOL MCP:
   - Sử dụng `hrm_search_employees` với tham số `q` là tên người vừa tìm được để lấy thông tin chi tiết của "nguoi_lap_to_trinh" và "lanh_dao_phe_duyet".
   - Sử dụng `tools_get_org_tree` để tìm mã và thông tin đầy đủ của "phong_ban".
Bước 3: Sau khi có kết quả từ Tool, mới tiến hành lắp ghép vào JSON theo format mẫu.

LƯU Ý QUAN TRỌNG:
- Nếu bạn trả về JSON với các ID giả như "DEPT001" hoặc email tự đoán, bạn sẽ thất bại.
- Mọi ID, Email, và Mã phòng ban PHẢI lấy từ kết quả trả về của Tool.
- Trả về kết quả cuối cùng là JSON nguyên khối.
DƯỚI ĐÂY LÀ FORMAT JSON BẮT BUỘC:
{
    "ten_tai_lieu": "string",
    "tieu_de": "string",
    "tom_tat": "string",
    "nam_ghi_nhan": number,
    "so_hieu_to_trinh": "string",
    "ngay_lap_tơ_trinh": "string",
    "nguoi_lap_to_trinh": {
        "objectValue": [
            {"key": "ten_dang_nhap", "label": "Tên", "value": "string", "is_show": true, "is_save": false},
            {"key": "email", "label": "Email", "value": "string", "is_show": true, "is_save": false},
            {"key": "phong_cap_1", "label": "Khối", "value": {"label": "string", "value": "string"}, "is_show": true, "is_save": false},
            {"key": "phong_ban_phu_trach", "label": "Ban", "value": {"_id": "string", "label": "string", "value": "string", "data_source": "danh_muc_don_vi_to_chuc_list", "display_member": "ten_don_vi_to_chuc", "value_member": "code"}, "is_show": true, "is_save": false}
        ],
        "option": {"_id": "string", "ten_email": "string"},
        "label": "string",
        "value": "string"
    },
    "loai_to_trinh": "string",
    "phong_ban": {
        "objectValue": [{"key": "code", "label": "Mã", "value": "string", "is_show": true, "is_save": false}],
        "option": {"_id": "string", "ten_don_vi_to_chuc": "string"},
        "label": "string",
        "value": "string"
    },
    "noi_dung_to_trinh": "string",
    "so_luong_luu_ban_cung": number,
    "lanh_dao_phe_duyet": { "objectValue": [...tương tự nguoi_lap...], "option": {...}, "label": "string", "value": "string" },
    "ghi_chu": "string"
}
"""

# Lookup: agent_id → base prompt (dùng khi inject instructions)
_AGENT_BASE_PROMPTS: dict[str, str] = {
    AGENT_ID_EMPLOYEE:   EMPLOYEE_AGENT_PROMPT,
    AGENT_ID_LEAVE:      LEAVE_INFO_AGENT_PROMPT,
    AGENT_ID_REQUEST:    REQUEST_AGENT_PROMPT,
    AGENT_ID_ATTENDANCE: ATTENDANCE_AGENT_PROMPT,
    AGENT_ID_ANALYTICS:  ANALYTICS_AGENT_PROMPT,
    AGENT_ID_OCR_DOC:    OCR_DOCUMENT_AGENT_PROMPT,
}


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
        # max_tokens=max_tokens,
        temperature=0.1,
        request_params={
            "tool_choice": "auto",
            # NOTE: Removed stream: False and stream_options to avoid vLLM validation error
            # vLLM rejects: "Stream options can only be defined when stream=True"
            # Agno will default to stream=False anyway
            "extra_body": {
                "enable_thinking": False,
                "thinking_budget": 0,
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
# CACHE — build một lần, tái dùng mọi request (học từ agents.py)
# ─────────────────────────────────────────────────────────────

_agents_cache: dict[str, Agent] = {}
_team_cache:   Team | None      = None
_agent_os:     AgentOS | None   = None


# ─────────────────────────────────────────────────────────────
# BUILDERS
# ─────────────────────────────────────────────────────────────

def _build_hrm_agents() -> list[Agent]:
    """
    Tạo 6 HRM specialized agents.
    - id=, name=: AgentOS nhận diện và expose endpoint.
    - description=: ngắn gọn, rõ PHẠM VI → Team router LLM đọc để chọn đúng agent.
    - instructions=: prompt đầy đủ, được inject session context lúc runtime.
    """
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )
    logger.info(f"✓ MCPTools initialized: url={settings.MCP_GATEWAY_URL}, transport=sse")

    common = dict(
        tools=[mcp],
        add_history_to_context=False,
        markdown=False,
    )

    return [
        Agent(
            id=AGENT_ID_EMPLOYEE,
            name="HRM Employee Agent",
            description=(
                "Tra cứu thông tin nhân viên HITC: hồ sơ, chức danh, phòng ban, "
                "thâm niên, danh sách nhân viên, tìm kiếm theo tên hoặc mã nhân viên."
            ),
            model=_make_model(max_tokens=512),
            instructions=[EMPLOYEE_AGENT_PROMPT],
            add_datetime_to_context=False,
            **common,
        ),
        Agent(
            id=AGENT_ID_LEAVE,
            name="HRM Leave Agent",
            description=(
                "Tra cứu quy định nghỉ phép và ngày nghỉ lễ HITC: ngày lễ, lịch nghỉ, "
                "loại nghỉ phép, phép năm, chính sách nghỉ, thứ 7, chủ nhật, ngày nghỉ bù."
            ),
            model=_make_model(max_tokens=512),
            instructions=[LEAVE_INFO_AGENT_PROMPT],
            add_datetime_to_context=True,
            **common,
        ),
        Agent(
            id=AGENT_ID_REQUEST,
            name="HRM Request Agent",
            description=(
                "Tra cứu đơn từ nhân sự HITC: đơn xin nghỉ phép, nghỉ ốm, đi muộn/về sớm, "
                "làm việc từ xa, công tác, trạng thái đơn, thống kê đơn từ nhân sự."
            ),
            model=_make_model(max_tokens=512),
            instructions=[REQUEST_AGENT_PROMPT],
            add_datetime_to_context=True,
            **common,
        ),
        Agent(
            id=AGENT_ID_ATTENDANCE,
            name="HRM Attendance Agent",
            description=(
                "Tra cứu chấm công thô HITC theo từng ngày cụ thể: check-in, check-out, "
                "giờ vào, giờ ra, tổng giờ làm một ngày, hôm nay vào lúc mấy giờ."
            ),
            model=_make_model(max_tokens=512),
            instructions=[ATTENDANCE_AGENT_PROMPT],
            add_datetime_to_context=True,
            **common,
        ),
        Agent(
            id=AGENT_ID_ANALYTICS,
            name="HRM Analytics Agent",
            description=(
                "Tổng hợp bảng chấm công tháng, xuất file Excel, gửi báo cáo qua email, "
                "tính công tháng theo phòng ban hoặc toàn công ty, download bảng công."
            ),
            model=_make_model(max_tokens=1024),
            instructions=[ANALYTICS_AGENT_PROMPT],
            add_datetime_to_context=True,
            **common,
        ),
        Agent(
            id=AGENT_ID_OCR_DOC,
            name="HRM OCR Document Agent",
            description=(
                "Xử lý văn bản OCR, tờ trình hành chính: trích xuất người lập, lãnh đạo phê duyệt, "
                "phòng ban, chuyển sang JSON chuẩn. "
                "Dùng khi query chứa: ocr, tờ trình, văn bản OCR, người lập, "
                "lãnh đạo phê duyệt, appended_content, HRM OCR Document Agent."
            ),
            model=_make_model(max_tokens=16384),
            instructions=[OCR_DOCUMENT_AGENT_PROMPT],
            # response_model=None,
            # structured_outputs=True,
            add_datetime_to_context=False,
            **common,
        ),
    ]


def _build_hrm_team(agents: list[Agent]) -> Team:
    """
    HRM Team — mode="route": LLM đọc description từng agent → chọn agent phù hợp.
    Được đăng ký vào AgentOS qua teams=[team].
    AgentOS expose: POST /hrm-agents/hrm-team/runs
    """
    return Team(
        id=TEAM_ID_HRM,
        name="HRM Team",
        description="Đội trợ lý AI nhân sự HITC — điều phối các agent chuyên biệt theo yêu cầu.",
        mode=TeamMode.route,
        # Router dùng model nhỏ: chỉ cần chọn agent, không sinh nội dung
        model=_make_model(max_tokens=16384),
        members=agents,
        # show_tool_calls=False,
        markdown=False,
    )


def _build_agent_os(team: Team) -> AgentOS:
    """
    AgentOS — control plane quản lý HRM Team.
    Giống agents.py: thử PostgresDb trước, fallback SqliteDb.
    Nhận teams=[team] — hierarchy AgentOS → Team → Agent.
    """
    try:
        db = PostgresDb(db_url=settings.AGENTOSAGNO_DB_URL)
        logger.info("✓ HRM AgentOS DB connected (PostgreSQL)")
    except Exception as e:
        logger.warning("⚠ HRM AgentOS DB fallback to SQLite: %s", e)
        db = SqliteDb(table_name="hrm_agentosagno_sessions")

    registry = Registry(
        name="HRM Registry",
        tools=[MCPTools(url=settings.MCP_GATEWAY_URL)],
        models=[
            OpenAILike(
                id=settings.LLM_MODEL,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "sk-",
            )
        ],
        dbs=[db],
    )

    return AgentOS(
        name="HRM AgentOS",
        description="AgentOS quản lý HRM Team và các HRM Agents của HITC.",
        teams=[team],       # ← AgentOS → Team → Agent (đúng hierarchy)
        db=db,
        registry=registry,
    )


# ─────────────────────────────────────────────────────────────
# CACHE ACCESSORS (học từ agents.py: _agents_cache + _get_agent)
# ─────────────────────────────────────────────────────────────

def _get_agents_cache() -> dict[str, Agent]:
    """Build agents một lần, cache theo id."""
    if not _agents_cache:
        for agent in _build_hrm_agents():
            _agents_cache[agent.id] = agent
    return _agents_cache


def _get_hrm_team() -> Team:
    """Build Team một lần từ agents cache."""
    global _team_cache
    if _team_cache is None:
        agents = list(_get_agents_cache().values())
        _team_cache = _build_hrm_team(agents)
        logger.info("✓ HRM Team initialized (%d agents, mode=route)", len(agents))
    return _team_cache


def _get_agent_os() -> AgentOS:
    """Build AgentOS một lần từ team cache."""
    global _agent_os
    if _agent_os is None:
        _agent_os = _build_agent_os(_get_hrm_team())
        logger.info("✓ HRM AgentOS initialized")
    return _agent_os


# ─────────────────────────────────────────────────────────────
# AGENTOS APP FACTORY
# ─────────────────────────────────────────────────────────────

def create_hrm_agent_os_app(base_app: Optional[FastAPI] = None) -> FastAPI:
    """
    Tạo và trả về FastAPI app của HRM AgentOS.
    Dùng trong main.py:

        from workflow.hrm_team import create_hrm_agent_os_app
        app = create_hrm_agent_os_app(base_app=app)

    Endpoints tự động:
        POST /hrm-agents/hrm-team/runs
    """
    kwargs: dict = {}
    if base_app is not None:
        kwargs["base_app"] = base_app
    return _get_agent_os().get_app(**kwargs)


# ─────────────────────────────────────────────────────────────
# RUNTIME CONTEXT INJECTION
# Inject session_id + user vào instructions trước mỗi request.
# OCR agent không inject session — chỉ giữ prompt gốc.
# ─────────────────────────────────────────────────────────────

def _inject_session_context(session_id: str, user: UserPermissionContext) -> None:
    runtime = _runtime_instructions(session_id, user)
    for aid, agent in _get_agents_cache().items():
        if aid == AGENT_ID_OCR_DOC:
            ocr_runtime = [
                f'session_id = "{session_id}"',
                f'username = "{user.username}"',
                "Dùng đúng session_id trên khi gọi tất cả tool: hrm_search_employees, tools_get_org_tree.",
                "KHÔNG thay đổi format output — chỉ trả về JSON thuần, không markdown.",
            ]
            agent.instructions = ocr_runtime + [OCR_DOCUMENT_AGENT_PROMPT]
        else:
            agent.instructions = runtime + [_AGENT_BASE_PROMPTS[aid]]


def _augmented_query(session_id: str, user: UserPermissionContext, query: str) -> str:
    return (
        f"[session_id:{session_id}] [username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] [company:{user.company_code}]\n"
        f"{query}"
    )


def _get_routed_agent_id(response) -> str:
    """Trích agent_id từ Team response. Fallback về EMPLOYEE."""
    try:
        if hasattr(response, "agent_id") and response.agent_id:
            return response.agent_id
        if hasattr(response, "member_responses") and response.member_responses:
            return getattr(response.member_responses[0], "agent_id", AGENT_ID_EMPLOYEE)
    except Exception:
        pass
    return AGENT_ID_EMPLOYEE


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE
# ─────────────────────────────────────────────────────────────
_OCR_KEYWORDS = {
    "ocr", "tờ trình", "văn bản ocr", "appended_content",
    "hrm ocr document agent", "số hiệu tờ trình", "người lập",
    "lãnh đạo phê duyệt", "page 0", "page 1",
}

def _is_ocr_request(query: str) -> bool:
    q_lower = query.lower()
    return any(kw in q_lower for kw in _OCR_KEYWORDS)
async def chat_with_hrm_team(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> dict:
    start = time.time()

    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    _inject_session_context(session_id, user)

    team      = _get_hrm_team()
    aug_query = _augmented_query(session_id, user, query)
    answer    = "Xin lỗi, có lỗi xảy ra."
    agent_id  = "unknown"

    try:
        # response = await team.arun(aug_query, session_id=session_id, user_id=user.user_id)
        # answer   = response.content if hasattr(response, "content") else str(response)
        # agent_id = _get_routed_agent_id(response)
        if _is_ocr_request(query):
            agent_id = AGENT_ID_OCR_DOC
            ocr_agent = _get_agents_cache()[AGENT_ID_OCR_DOC]
            answer  = await ocr_agent.arun(aug_query, session_id=session_id, user_id=user.user_id)
            # answer   = response.content if hasattr(response, "content") else str(response)
        else:
            # team     = _get_hrm_team()
            response = await team.arun(aug_query, session_id=session_id, user_id=user.user_id)
            answer   = response.content if hasattr(response, "content") else str(response)
            agent_id = _get_routed_agent_id(response)

    except Exception as e:
        logger.error("HRM Team error: session=%s user=%s error=%s", session_id, user.username, e, exc_info=True)
        answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info("HRM Team: routed_to=%s session=%s user=%s %.2fs", agent_id, session_id, user.username, duration)

    return {
        "session_id": session_id,
        "answer":     answer,
        "team":       "HRM Team",
        "agents":     [agent_id],
        "sources":    [],
        "metrics":    {"total_duration": duration, "agent_id": agent_id},
    }


# ─────────────────────────────────────────────────────────────
# SSE STREAM BRIDGE
# ─────────────────────────────────────────────────────────────

async def stream_with_hrm_team(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
) -> AsyncGenerator[str, None]:
    """
    Async generator trả về SSE event string.
    Dùng với FastAPI StreamingResponse(media_type="text/event-stream").

    Event format:
      data: {"type": "token",  "content": "..."}\\n\\n
      data: {"type": "tool",   "name": "...", "status": "start"|"end"}\\n\\n
      data: {"type": "done",   "session_id": "...", "agent_id": "..."}\\n\\n
      data: {"type": "error",  "message": "..."}\\n\\n
    """
    start = time.time()
    logger.info("HRM SSE start | query=%s", query[:60])

    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    _inject_session_context(session_id, user)

    team        = _get_hrm_team()
    aug_query   = _augmented_query(session_id, user, query)
    full_answer = ""
    agent_id    = "unknown"
    queue: asyncio.Queue = asyncio.Queue()

    def _run_sync():
        try:
            for chunk in team.run(
                aug_query,
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

    asyncio.get_event_loop().run_in_executor(None, _run_sync)

    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            if isinstance(chunk, Exception):
                raise chunk

            event_type = getattr(chunk, "event", None)

            if hasattr(chunk, "agent_id") and chunk.agent_id:
                agent_id = chunk.agent_id

            if event_type == "ToolCallStarted":
                yield _sse({"type": "tool", "name": getattr(chunk, "tool_name", "unknown"), "status": "start"})

            elif event_type == "ToolCallCompleted":
                yield _sse({"type": "tool", "name": getattr(chunk, "tool_name", "unknown"), "status": "end"})

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
        logger.error("HRM SSE error: session=%s user=%s error=%s", session_id, user.username, e, exc_info=True)
        yield _sse({"type": "error", "message": str(e)})
        full_answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": full_answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info("HRM SSE done: routed_to=%s session=%s user=%s %.2fs", agent_id, session_id, user.username, duration)
    yield _sse({
        "type":       "done",
        "session_id": session_id,
        "agent_id":   agent_id,
        "team":       "HRM Team",
        "metrics":    {"total_duration": duration},
    })


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"