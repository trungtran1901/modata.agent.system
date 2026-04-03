"""
workflow/agents.py

Agno AgentOS — Multi-Agent System cho HITC.

AgentOS là production runtime + control plane điều phối agents.
Pattern đúng theo docs:
  - Mỗi Agent có db= để AgentOS quản lý session/history
  - AgentOS(agents=[...]) → app = agent_os.get_app()
  - app được mount vào FastAPI app hiện tại (base_app=)

Tích hợp vào app hiện tại:
  # main.py hoặc app startup
  from workflow.agents import create_agent_os_app
  agent_os_app = create_agent_os_app(base_app=app)

Endpoints tự động:
  POST /agents/checkin-agent/runs
  POST /agents/data-query-agent/runs
  POST /agents/analytics-agent/runs
  ...

Session/context cho modata-mcp vẫn lưu qua session_store (PG + Redis).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
from agno.models.openai.like import OpenAILike
from agno.os import AgentOS
from agno.registry import Registry
from agno.tools.mcp import MCPTools
from fastapi import FastAPI

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

# Agent IDs
AGENT_ID_CHECKIN     = "checkin-agent"
AGENT_ID_DATA_QUERY  = "data-query-agent"
AGENT_ID_ANALYTICS   = "analytics-agent"
AGENT_ID_EMAIL       = "email-agent"
AGENT_ID_SEARCH_DOCS = "search-docs-agent"


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────

PROMPT_CHECKIN = """\
Bạn là trợ lý AI nội bộ HITC, chuyên xử lý chấm công, giờ vào ra.

COLLECTION: lich_su_cham_cong_tong_hop_cong

WORKFLOW:
  1. tools_get_current_time(format="date") → ngày hôm nay
  2. data_query_collection(sid, "lich_su_cham_cong_tong_hop_cong",
       {"ten_dang_nhap": "[username]", "ngay": "<date>"}, limit=1)
  3. Trả lời giờ vào/ra, tổng giờ làm

LƯU Ý:
  - "của tôi" → filter {"ten_dang_nhap": "[username]"} (username trong context)
  - Format giờ: HH:MM (24h)

Trả lời tiếng Việt, ngắn gọn, chính xác.
"""

PROMPT_DATA_QUERY = """\
Bạn là trợ lý AI nội bộ HITC, chuyên query nhân viên, hợp đồng, phép, thiết bị.

MAPPING COLLECTION:
  nhân viên/hồ sơ/lương/chức vụ → thong_tin_nhan_vien
  hợp đồng                       → hop_dong_lao_dong
  nghỉ phép/đơn nghỉ             → don_nghi_phep
  thiết bị/tài sản               → danh_sach_thiet_bi
  đào tạo/khóa học               → lich_su_dao_tao
  khen thưởng                    → lich_su_khen_thuong
  kỷ luật                        → lich_su_ky_luat

RULES:
  - "của tôi" → filter {"ten_dang_nhap": "[username]"}
  - Filter chính xác → data_query_collection() thay vì search
  - Không chắc collection → data_get_schema() để xem fields trước

TOOLS:
  data_query_collection(sid, collection, filter, limit=3)
  data_find_one(sid, collection, filter)
  data_search_records(sid, collection, keyword)
  data_get_schema(sid, collection)

Trả lời ngắn gọn, có dữ liệu cụ thể.
"""

PROMPT_ANALYTICS = """\
Bạn là trợ lý AI nội bộ HITC, chuyên thống kê, count, group by, aggregate.

OPERATIONS:
  analytics_count(sid, collection, filter)
  analytics_group_by_field(sid, collection, group_field, aggregate_field)
  analytics_aggregate(sid, collection, pipeline)

EXAMPLES:
  - "Có bao nhiêu nhân viên?" → analytics_count(sid, "thong_tin_nhan_vien")
  - "Lương trung bình?"       → analytics_aggregate(...)
  - "Nhân viên theo phòng ban?" → analytics_group_by_field(..., "phong_ban", ...)

Luôn kết thúc bằng số cụ thể. Trả lời tiếng Việt.
"""

PROMPT_EMAIL = """\
Bạn là trợ lý AI nội bộ HITC, chuyên gửi email và thông báo.

TOOLS:
  mail_send_email(to, subject, body, attachments)
  mail_send_email_to_team(team, subject, body)

RULES:
  - Subject rõ ràng, ngắn gọn
  - Body: có greeting, nội dung, signature
  - Hỏi xác nhận trước khi gửi
"""

PROMPT_SEARCH_DOCS = """\
Bạn là trợ lý AI nội bộ HITC, chuyên tìm kiếm tài liệu nội bộ.

TOOL:
  docs_search_docs(query, limit=5, filters={})

EXAMPLES:
  - "Quy định làm việc?"   → docs_search_docs("quy định làm việc")
  - "Chính sách kỳ nghỉ?" → docs_search_docs("chính sách kỳ nghỉ")

Trả lời: tài liệu gợi ý + summary ngắn.
"""


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _make_model(max_tokens: int = 1024, temperature: float = 0.5) -> OpenAILike:
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




def _make_db() -> SqliteDb:
    """AgentOS dùng SqliteDb để quản lý session/history của agents."""
    # Không dùng SqliteDb - Agno AgentOS tự manage history
    # Session store được save vào PostgreSQL thay vì SQLite
    pass


# ─────────────────────────────────────────────────────────────
# AGENT BUILDERS
# Theo docs: Agent có db= → AgentOS tự quản lý history/session
# ─────────────────────────────────────────────────────────────

def _build_agents() -> list[Agent]:
    """
    Tạo tất cả specialized agents.
    Mỗi agent có:
      - agent_id:  để AgentOS serve tại /agents/{agent_id}/runs
      - db:        để AgentOS quản lý session + history
      - tools:     MCPTools kết nối modata-mcp
    """
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )

    common = dict(
        tools=[mcp],
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=3,
        markdown=False,
    )

    return [
        Agent(
            id=AGENT_ID_CHECKIN,
            name="Check-in Agent",
            model=_make_model(max_tokens=512, temperature=0.3),
            description=PROMPT_CHECKIN,
            **common,
        ),
        Agent(
            id=AGENT_ID_DATA_QUERY,
            name="Data Query Agent",
            model=_make_model(max_tokens=1024, temperature=0.5),
            description=PROMPT_DATA_QUERY,
            **common,
        ),
        Agent(
            id=AGENT_ID_ANALYTICS,
            name="Analytics Agent",
            model=_make_model(max_tokens=512, temperature=0.2),
            description=PROMPT_ANALYTICS,
            **common,
        ),
        Agent(
            id=AGENT_ID_EMAIL,
            name="Email Agent",
            model=_make_model(max_tokens=512, temperature=0.5),
            description=PROMPT_EMAIL,
            **common,
        ),
        Agent(
            id=AGENT_ID_SEARCH_DOCS,
            name="Search Docs Agent",
            model=_make_model(max_tokens=1024, temperature=0.3),
            description=PROMPT_SEARCH_DOCS,
            **common,
        ),
    ]


# ─────────────────────────────────────────────────────────────
# AGENTOS FACTORY
# ─────────────────────────────────────────────────────────────

def create_agent_os_app(base_app: Optional[FastAPI] = None) -> FastAPI:
    """
    Tạo AgentOS với Database + Registry và trả về FastAPI app.

    Database được sử dụng bởi:
      - AgentOS Studio để lưu agents, teams, workflows
      - Session management để track agent runs
      - Metrics và tracing

    Nếu base_app được truyền vào, AgentOS mount thêm routes của nó
    vào app hiện tại (BYO FastAPI pattern).

    Để kết nối với Control Plane (os.agno.com):
      1. Deploy AgentOS này lên server HTTPS (hoặc localhost dev)
      2. Vào os.agno.com → Add new OS
      3. Điền:
         - Environment: Local (http://localhost:8000) hoặc Live
         - Endpoint URL: {settings.AGENTOSAGNO_ENDPOINT}
         - OS Name: {settings.AGENTOSAGNO_NAME}
         - Tags: dev, stg, prd (tùy chọn)
      4. Click "CONNECT" → AgentOS sẽ xuất hiện trong dashboard
      5. Mở Studio → build agents, teams, workflows visually
         → Save, Test, Publish → Use via API

    Cách dùng trong main.py:
        from workflow.agents import create_agent_os_app
        app = create_agent_os_app(base_app=app)

    Hoặc chạy standalone:
        app = create_agent_os_app()
        # fastapi dev agents.py
    """
    agents = _build_agents()

    # Database cho AgentOS Studio
    # Studio cần database để save/load agents, teams, workflows
    try:
        db = PostgresDb(db_url=settings.AGENTOSAGNO_DB_URL)
        logger.info("✓ AgentOS Database connected: %s", settings.AGENTOSAGNO_DB_NAME)
    except Exception as e:
        logger.warning(
            "⚠ Cannot connect to AgentOS DB (%s), using SQLite fallback: %s",
            settings.AGENTOSAGNO_DB_URL,
            str(e),
        )
        db = SqliteDb(table_name="agentosagno_sessions")

    # Registry cho Studio
    # Cho phép build agencies, teams, workflows từ available tools/models/dbs
    registry = Registry(
        name=f"{settings.AGENTOSAGNO_NAME} Registry",
        tools=[MCPTools(url=settings.MCP_GATEWAY_URL)],
        models=[
            OpenAILike(
                id=settings.LLM_MODEL,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "sk-",
            )
        ],
        dbs=[db],  # Studio requires db parameter
    )

    kwargs: dict = dict(
        name=settings.AGENTOSAGNO_NAME,
        description=settings.AGENTOSAGNO_DESCRIPTION,
        agents=agents,
        db=db,  # For session/run tracking
        registry=registry,  # For Studio
    )
    if base_app is not None:
        kwargs["base_app"] = base_app

    agent_os = AgentOS(**kwargs)

    logger.info(
        "✓ AgentOS initialized: %s (%d agents)",
        settings.AGENTOSAGNO_NAME,
        len(agents),
    )
    logger.info(
        "✓ To connect to Control Plane, go to os.agno.com and add: %s",
        settings.AGENTOSAGNO_ENDPOINT,
    )
    logger.info("✓ Studio Registry available with tools and models for visual building")

    # get_app() trả về FastAPI app đã gắn tất cả routes AgentOS
    return agent_os.get_app()


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE
# Dùng khi cần tích hợp với chat endpoint hiện tại (/chat).
# Lưu context cho modata-mcp, sau đó gọi đúng agent theo keyword.
# ─────────────────────────────────────────────────────────────

def _decide_agent_id(query: str) -> str:
    q = query.lower()
    if any(kw in q for kw in [
        "chấm công", "giờ vào", "giờ ra", "check-in", "check in",
        "vào lúc", "ra lúc", "hôm nay làm", "bao nhiêu giờ",
    ]):
        return AGENT_ID_CHECKIN
    if any(kw in q for kw in [
        "bao nhiêu", "tổng", "trung bình", "thống kê", "count", "tính tổng",
    ]):
        return AGENT_ID_ANALYTICS
    if any(kw in q for kw in ["gửi email", "gửi thông báo", "email"]):
        return AGENT_ID_EMAIL
    if any(kw in q for kw in ["quy định", "chính sách", "tài liệu", "nội quy"]):
        return AGENT_ID_SEARCH_DOCS
    return AGENT_ID_DATA_QUERY


# Cache agents để không tạo lại mỗi request
_agents_cache: dict[str, Agent] = {}

def _get_agent(agent_id: str) -> Agent:
    if not _agents_cache:
        for agent in _build_agents():
            _agents_cache[agent.id] = agent
    return _agents_cache[agent_id]


async def chat_with_agentosagno(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
    app_module: Optional[str] = None,
) -> dict:
    """
    Bridge giữa /chat endpoint hiện tại và AgentOS agents.

    1. Lưu context vào session_store (PG + Redis) cho modata-mcp
    2. Chọn agent theo keyword
    3. Gọi agent.arun() trực tiếp với session_id + user context
    AgentOS quản lý history qua db=SqliteDb.
    """
    start = time.time()

    # Lưu context để modata-mcp kiểm tra quyền theo session_id
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    # Augmented query với user context (giống agent.py)
    augmented_query = (
        f"[session_id:{session_id}] [username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] [company:{user.company_code}]\n"
        f"{query}"
    )

    # Chọn agent phù hợp
    agent_id = _decide_agent_id(query)
    agent    = _get_agent(agent_id)
    logger.info("Routing → %s", agent_id)

    try:
        # Gọi agent trực tiếp — AgentOS quản lý session/history qua db
        response = await agent.arun(
            augmented_query,
            session_id=session_id,
            user_id=user.user_id,
        )
        answer = response.content if hasattr(response, "content") else str(response)
    except Exception as e:
        logger.error("Agent error: %s", e, exc_info=True)
        answer = f"Lỗi xử lý: {str(e)}"

    return {
        "session_id": session_id,
        "answer":     answer,
        "sources":    [],
        "metrics": {
            "total_duration": time.time() - start,
            "agent_id":       agent_id,
        },
    }


# ─────────────────────────────────────────────────────────────
# STANDALONE — chạy trực tiếp file này
# ─────────────────────────────────────────────────────────────

app = create_agent_os_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("workflow.agents:app", host="0.0.0.0", port=7777, reload=True)