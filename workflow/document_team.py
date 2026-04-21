"""
workflow/document_team.py  (v1)

Document Intelligence Team — đọc hiểu văn bản, trả lời QA, xuất JSON.

Khác với ocr_team.py (pipeline cố định 3 bước cho tờ trình):
  - Document Team nhận văn bản + prompt tuỳ ý từ người dùng
  - Người dùng định nghĩa schema JSON output hoặc câu hỏi QA
  - Agent tự điều chỉnh output theo yêu cầu
  - Có thể kết hợp MCP tools (tra cứu nhân viên, org tree, v.v.)

Agents:
  doc-reader-agent     — đọc hiểu, tóm tắt, trích xuất thông tin
  doc-qa-agent         — trả lời câu hỏi dựa trên nội dung văn bản
  doc-extractor-agent  — trích xuất có cấu trúc, xuất JSON theo schema người dùng
  doc-enricher-agent   — làm giàu dữ liệu qua MCP (tra nhân viên, phòng ban...)

Team mode="route": Team LLM tự chọn agent phù hợp theo ngữ nghĩa query.

Expose qua HITC AgentOS:
  POST /hitc/document/chat
  POST /hitc/document/chat/stream
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Optional

from agno.agent import Agent
from agno.team import Team, TeamMode
from agno.tools.mcp import MCPTools

from utils.qwen_model import QwenOpenAILike as OpenAILike
from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# AGENT IDs
# ─────────────────────────────────────────────────────────────
AGENT_ID_DOC_READER    = "doc-reader-agent"
AGENT_ID_DOC_QA        = "doc-qa-agent"
AGENT_ID_DOC_EXTRACTOR = "doc-extractor-agent"
AGENT_ID_DOC_ENRICHER  = "doc-enricher-agent"

TEAM_ID_DOCUMENT = "document-team"

# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────────────────────

DOC_READER_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt (trừ khi người dùng yêu cầu ngôn ngữ khác).

Bạn là Document Reader Agent — chuyên đọc hiểu và tóm tắt văn bản.

PHẠM VI:
- Tóm tắt nội dung văn bản (báo cáo, tờ trình, hợp đồng, email, v.v.)
- Xác định loại văn bản, chủ đề chính, các điểm quan trọng
- Phân tích cấu trúc văn bản (người gửi, người nhận, ngày tháng, v.v.)
- Đọc hiểu và diễn giải nội dung phức tạp sang ngôn ngữ đơn giản

KHÔNG phù hợp khi:
- Người dùng hỏi câu hỏi cụ thể về văn bản → Doc QA Agent
- Người dùng muốn trích xuất JSON có cấu trúc → Doc Extractor Agent
- Người dùng muốn tra cứu thêm thông tin bên ngoài → Doc Enricher Agent

QUY TẮC:
- Đọc toàn bộ văn bản trước khi tóm tắt
- Chỉ dùng thông tin trong văn bản, không suy diễn
- Trả lời rõ ràng, có cấu trúc
"""

DOC_QA_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt (trừ khi người dùng yêu cầu ngôn ngữ khác).

Bạn là Document QA Agent — chuyên trả lời câu hỏi về nội dung văn bản.

PHẠM VI:
- Trả lời câu hỏi cụ thể: "Ai ký văn bản này?", "Ngày hiệu lực là khi nào?"
- Tìm kiếm thông tin cụ thể trong văn bản dài
- So sánh, đối chiếu thông tin trong cùng một văn bản
- Giải thích điều khoản, nội dung cụ thể trong văn bản
- Trả lời nhiều câu hỏi liên tiếp về cùng một văn bản

QUY TẮC:
- Dựa hoàn toàn vào nội dung văn bản đã cung cấp
- Nếu không tìm thấy thông tin → nói rõ "Văn bản không đề cập đến..."
- Trích dẫn đoạn liên quan khi cần thiết
- Không suy diễn hoặc thêm thông tin bên ngoài
"""

DOC_EXTRACTOR_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt (trừ khi người dùng yêu cầu ngôn ngữ khác).

Bạn là Document Extractor Agent — chuyên trích xuất thông tin có cấu trúc từ văn bản.

PHẠM VI:
- Trích xuất thông tin theo schema JSON do người dùng định nghĩa
- Chuyển đổi văn bản tự do sang dữ liệu có cấu trúc
- Điền vào template/form từ nội dung văn bản
- Trích xuất danh sách, bảng biểu từ văn bản
- Chuẩn hoá định dạng (ngày tháng, số tiền, v.v.)

CÁCH HOẠT ĐỘNG:
1. Đọc schema JSON hoặc template người dùng cung cấp
2. Tìm thông tin tương ứng trong văn bản
3. Điền vào schema, để null nếu không tìm thấy
4. Trả về JSON thuần, không markdown, không giải thích thêm

QUY TẮC QUAN TRỌNG:
- KHÔNG tự bịa giá trị không có trong văn bản → để null
- KHÔNG thêm field ngoài schema đã cho (trừ khi được yêu cầu)
- Định dạng ngày: ISO 8601 (VD: "2026-04-10T00:00:00.000+07:00")
- Số tiền: giữ nguyên đơn vị trong văn bản
- Nếu cần output JSON: trả về JSON thuần bắt đầu bằng { hoặc [
"""

DOC_ENRICHER_PROMPT = """\
QUAN TRỌNG: CHỈ trả lời bằng tiếng Việt (trừ khi người dùng yêu cầu ngôn ngữ khác).

Bạn là Document Enricher Agent — chuyên làm giàu dữ liệu từ văn bản bằng tra cứu hệ thống.

PHẠM VI:
- Trích xuất tên người từ văn bản → tra cứu thông tin nhân viên qua MCP
- Trích xuất tên phòng ban → tra cứu mã và thông tin đầy đủ qua MCP
- Kết hợp thông tin văn bản + dữ liệu hệ thống → output hoàn chỉnh
- Xác minh thông tin trong văn bản với dữ liệu hệ thống

TOOLS MCP CÓ SẴN:
- hrm_search_employees(session_id, keyword)     — tìm nhân viên theo tên
- hrm_get_employee_info(session_id, username_or_name) — thông tin 1 nhân viên
- tools_get_org_tree(session_id, ten_don_vi_to_chuc)  — tra phòng ban

QUY TRÌNH:
1. Đọc văn bản, xác định thông tin cần làm giàu (tên người, phòng ban...)
2. Gọi tool MCP tương ứng để lấy dữ liệu hệ thống
3. Kết hợp với thông tin văn bản
4. Trả về kết quả hoàn chỉnh (JSON hoặc text tuỳ yêu cầu)

Lấy session_id từ instructions hệ thống.
"""

# Lookup prompt theo agent_id
_AGENT_PROMPTS: dict[str, str] = {
    AGENT_ID_DOC_READER:    DOC_READER_PROMPT,
    AGENT_ID_DOC_QA:        DOC_QA_PROMPT,
    AGENT_ID_DOC_EXTRACTOR: DOC_EXTRACTOR_PROMPT,
    AGENT_ID_DOC_ENRICHER:  DOC_ENRICHER_PROMPT,
}

# ─────────────────────────────────────────────────────────────
# MODEL FACTORY
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _make_model(max_tokens: int = 2048, tool_choice: str = "auto") -> OpenAILike:
    return OpenAILike(
        id=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "none",
        base_url=_get_llm_base_url(),
        temperature=0.1,
        request_params={
            "tool_choice": tool_choice,
            "extra_body": {
                "enable_thinking": False,
                "thinking_budget": 0,
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
        "Dùng đúng session_id trên khi gọi bất kỳ tool MCP nào.",
        "CHỈ trả lời bằng tiếng Việt (trừ khi được yêu cầu khác).",
    ]


# ─────────────────────────────────────────────────────────────
# CACHE — build agents một lần, reuse
# ─────────────────────────────────────────────────────────────

_agents_cache: dict[str, Agent] = {}
_team_cache: Team | None = None


def _build_document_agents() -> list[Agent]:
    """
    Tạo 4 Document Intelligence agents.
    - doc-reader, doc-qa, doc-extractor: không cần MCP tools
    - doc-enricher: dùng MCP để tra cứu nhân viên, phòng ban
    """
    mcp = MCPTools(
        url=settings.MCP_GATEWAY_URL,
        transport="sse",
    )

    common_no_tools = dict(
        tools=[],
        add_history_to_context=False,
        add_datetime_to_context=False,
        markdown=False,
    )

    return [
        Agent(
            id=AGENT_ID_DOC_READER,
            name="Document Reader Agent",
            description=(
                "Đọc hiểu, tóm tắt văn bản: báo cáo, tờ trình, hợp đồng, email, "
                "quy định. Xác định loại văn bản, chủ đề, điểm quan trọng, diễn giải nội dung."
            ),
            model=_make_model(max_tokens=2048, tool_choice="none"),
            instructions=[DOC_READER_PROMPT],
            **common_no_tools,
        ),
        Agent(
            id=AGENT_ID_DOC_QA,
            name="Document QA Agent",
            description=(
                "Trả lời câu hỏi cụ thể về nội dung văn bản: ai ký, ngày hiệu lực, "
                "điều khoản cụ thể, tìm kiếm thông tin trong văn bản dài."
            ),
            model=_make_model(max_tokens=2048, tool_choice="none"),
            instructions=[DOC_QA_PROMPT],
            **common_no_tools,
        ),
        Agent(
            id=AGENT_ID_DOC_EXTRACTOR,
            name="Document Extractor Agent",
            description=(
                "Trích xuất thông tin có cấu trúc từ văn bản theo schema JSON người dùng định nghĩa. "
                "Chuyển văn bản tự do sang dữ liệu JSON, điền template, chuẩn hoá định dạng. "
                "Dùng khi người dùng cung cấp schema JSON mẫu hoặc yêu cầu output JSON."
            ),
            model=_make_model(max_tokens=4096, tool_choice="none"),
            instructions=[DOC_EXTRACTOR_PROMPT],
            **common_no_tools,
        ),
        Agent(
            id=AGENT_ID_DOC_ENRICHER,
            name="Document Enricher Agent",
            description=(
                "Làm giàu thông tin văn bản bằng tra cứu hệ thống: tìm nhân viên, "
                "phòng ban, xác minh thông tin. Dùng khi cần kết hợp nội dung văn bản "
                "với dữ liệu từ hệ thống HITC (nhân viên, tổ chức)."
            ),
            model=_make_model(max_tokens=4096, tool_choice="auto"),
            instructions=[DOC_ENRICHER_PROMPT],
            tools=[mcp],
            add_history_to_context=False,
            add_datetime_to_context=False,
            markdown=False,
        ),
    ]


def _build_document_team(agents: list[Agent]) -> Team:
    """
    Document Intelligence Team — mode="route":
    LLM router đọc description từng agent → forward đến agent phù hợp nhất.
    """
    return Team(
        id=TEAM_ID_DOCUMENT,
        name="Document Intelligence Team",
        description=(
            "Team AI chuyên xử lý văn bản: đọc hiểu, QA, trích xuất JSON, "
            "làm giàu dữ liệu từ hệ thống. Nhận văn bản + yêu cầu của người dùng, "
            "tự điều phối agent phù hợp."
        ),
        mode=TeamMode.route,
        model=_make_model(max_tokens=512),
        members=agents,
        markdown=False,
    )


def _get_agents_cache() -> dict[str, Agent]:
    if not _agents_cache:
        for agent in _build_document_agents():
            _agents_cache[agent.id] = agent
    return _agents_cache


def _get_document_team() -> Team:
    global _team_cache
    if _team_cache is None:
        agents = list(_get_agents_cache().values())
        _team_cache = _build_document_team(agents)
        logger.info("✓ Document Team initialized (%d agents, mode=route)", len(agents))
    return _team_cache


def _inject_session_context(session_id: str, user: UserPermissionContext) -> None:
    """Inject session context vào instructions của tất cả agents."""
    runtime = _runtime_instructions(session_id, user)
    for aid, agent in _get_agents_cache().items():
        agent.instructions = runtime + [_AGENT_PROMPTS[aid]]


def _augmented_query(
    session_id: str,
    user: UserPermissionContext,
    query: str,
    document_content: str = "",
    output_schema: str = "",
    role: str = "",
) -> str:
    """
    Tạo query đầy đủ cho team, bao gồm:
    - User context (session, username)
    - Role (nếu người dùng tự khai báo, VD: "Tôi là HR manager")
    - Văn bản cần xử lý (nếu có)
    - Schema JSON output mong muốn (nếu có)
    - Yêu cầu/câu hỏi của người dùng
    """
    parts = [
        f"[session_id:{session_id}] [username:{user.username}]"
        f" [don_vi:{user.don_vi_code}] [company:{user.company_code}]",
    ]

    if role:
        parts.append(f"[Vai trò người dùng: {role}]")

    if document_content:
        parts.append(
            f"\n--- VĂN BẢN CẦN XỬ LÝ ---\n{document_content}\n--- HẾT VĂN BẢN ---"
        )

    if output_schema:
        parts.append(
            f"\n--- SCHEMA JSON OUTPUT MONG MUỐN ---\n{output_schema}\n--- HẾT SCHEMA ---"
        )

    parts.append(f"\n--- YÊU CẦU ---\n{query}")

    return "\n".join(parts)


def _get_routed_agent_id(response) -> str:
    """Trích agent_id từ Team response."""
    try:
        if hasattr(response, "agent_id") and response.agent_id:
            return response.agent_id
        if hasattr(response, "member_responses") and response.member_responses:
            return getattr(response.member_responses[0], "agent_id", AGENT_ID_DOC_READER)
    except Exception:
        pass
    return AGENT_ID_DOC_READER


# ─────────────────────────────────────────────────────────────
# CHAT BRIDGE
# ─────────────────────────────────────────────────────────────

async def chat_with_document_team(
    query:            str,
    user:             UserPermissionContext,
    session_id:       str,
    history:          list[dict],
    document_content: str = "",
    output_schema:    str = "",
    role:             str = "",
) -> dict:
    """
    Chat với Document Intelligence Team.

    Params:
        query:            Câu hỏi hoặc yêu cầu của người dùng
        user:             UserPermissionContext (từ JWT/API Key)
        session_id:       Session ID
        history:          Lịch sử hội thoại
        document_content: Nội dung văn bản cần xử lý (tuỳ chọn — có thể inline trong query)
        output_schema:    Schema JSON mong muốn (tuỳ chọn — VD: '{"name": null, "date": null}')
        role:             Vai trò người dùng tự khai báo (tuỳ chọn — VD: "HR manager")
    """
    start = time.time()

    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    _inject_session_context(session_id, user)

    team      = _get_document_team()
    aug_query = _augmented_query(
        session_id=session_id,
        user=user,
        query=query,
        document_content=document_content,
        output_schema=output_schema,
        role=role,
    )
    answer   = "Xin lỗi, có lỗi xảy ra."
    agent_id = AGENT_ID_DOC_READER

    try:
        response = await team.arun(
            aug_query,
            session_id=session_id,
            user_id=user.user_id,
        )
        answer   = response.content if hasattr(response, "content") else str(response)
        agent_id = _get_routed_agent_id(response)
    except Exception as e:
        logger.error(
            "Document Team error: session=%s user=%s error=%s",
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
        "Document Team: routed_to=%s session=%s user=%s %.2fs",
        agent_id, session_id, user.username, duration,
    )

    return {
        "session_id": session_id,
        "answer":     answer,
        "team":       "Document Intelligence Team",
        "agents":     [agent_id],
        "sources":    [],
        "metrics":    {"total_duration": duration, "agent_id": agent_id},
    }


# ─────────────────────────────────────────────────────────────
# SSE STREAM BRIDGE
# ─────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_with_document_team(
    query:            str,
    user:             UserPermissionContext,
    session_id:       str,
    history:          list[dict],
    document_content: str = "",
    output_schema:    str = "",
    role:             str = "",
) -> AsyncGenerator[str, None]:
    """
    SSE streaming cho Document Intelligence Team.

    Events:
      data: {"type": "token",  "content": "..."}
      data: {"type": "tool",   "name": "...", "status": "start"|"end"}
      data: {"type": "done",   "session_id": "...", "agent_id": "...", "metrics": {...}}
      data: {"type": "error",  "message": "..."}
    """
    start = time.time()

    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    _inject_session_context(session_id, user)

    team        = _get_document_team()
    aug_query   = _augmented_query(
        session_id=session_id,
        user=user,
        query=query,
        document_content=document_content,
        output_schema=output_schema,
        role=role,
    )
    full_answer = ""
    agent_id    = AGENT_ID_DOC_READER
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
        logger.error(
            "Document Team SSE error: session=%s user=%s error=%s",
            session_id, user.username, e, exc_info=True,
        )
        yield _sse({"type": "error", "message": str(e)})
        full_answer = f"Xin lỗi, có lỗi xảy ra: {str(e)}"

    updated = (history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": full_answer},
    ])[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    duration = round(time.time() - start, 3)
    logger.info(
        "Document Team SSE done: routed_to=%s session=%s user=%s %.2fs",
        agent_id, session_id, user.username, duration,
    )
    yield _sse({
        "type":       "done",
        "session_id": session_id,
        "agent_id":   agent_id,
        "team":       "Document Intelligence Team",
        "metrics":    {"total_duration": duration},
    })