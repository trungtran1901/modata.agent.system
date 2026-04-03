"""
workflow/agent.py

Agno Agent kết nối MCP Gateway (modata-mcp) qua SSE transport.
Nhận query từ /chat → gọi tools → trả về answer.

Token budget (context window 16,384):
  SYSTEM_PROMPT      ~1,000
  Tool definitions   ~1,400  (21 tools × ~65 tokens)
  Instructions       ~150
  augmented_query    ~80
  Tool results       ~2,000  (truncated, xem TOOL_RESULT_MAX_CHARS)
  History            ~600    (RAG_MAX_HISTORY=3 turns × ~100 tokens)
  Output reserve     ~1,500
  ─────────────────────────
  Total budget       ~6,730  → an toàn dưới 16,384
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.tools.mcp import MCPTools

from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

# Giới hạn ký tự của mỗi tool result trước khi đưa vào context
# ~2,000 chars ≈ 600–700 tokens — đủ để AI đọc thông tin quan trọng
TOOL_RESULT_MAX_CHARS = 2_000


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT — súc tích, chỉ giữ thông tin LLM cần
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
Bạn là trợ lý AI nội bộ HITC. Gọi tools để trả lời, không đoán mò.

QUY TẮC:
- Result data_* đã flatten: key = tiếng Việt. Filter dùng field_name kỹ thuật.
- "của tôi" → filter {"ten_dang_nhap": "[username]"} (username trong header query).
- Ngày hôm nay → tools_get_current_time(format="date").

MAPPING COLLECTION:
  chấm công/check-in/giờ vào ra → lich_su_cham_cong_tong_hop_cong
  nhân viên/hồ sơ/lương/chức vụ → thong_tin_nhan_vien
  hợp đồng                       → hop_dong_lao_dong
  nghỉ phép/đơn nghỉ             → don_nghi_phep
  thiết bị/tài sản               → danh_sach_thiet_bi
  đào tạo/khóa học               → lich_su_dao_tao
  khen thưởng/kỷ luật            → lich_su_khen_thuong / lich_su_ky_luat
  (không chắc → hỏi lại user hoặc thử instance_name hợp lý)

TOOLS:
  data_search_records(sid, collection, keyword)     — tìm full-text
  data_query_collection(sid, collection, filter, limit=3) — filter chính xác
  data_find_one(sid, collection, filter)            — 1 bản ghi
  data_get_schema(sid, collection)                  — xem fields
  analytics_count/group_by_field/aggregate          — thống kê
  tools_get_current_time/calculate_service_time/calculate_working_days
  tools_lookup_danhmuc / tools_get_org_tree
  mail_send_email / mail_send_email_to_team
  docs_search_docs(query, ...)                      — tài liệu nội bộ

WORKFLOW check-in:
  1. tools_get_current_time(format="date") → ngày hôm nay
  2. data_query_collection(sid, "lich_su_cham_cong_tong_hop_cong",
       {"ten_dang_nhap":"[username]","ngay":"<date>"}, limit=1)
  3. Trả lời giờ vào/ra

Trả lời tiếng Việt, ngắn gọn.\
"""


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _trim_history(history: list[dict], max_turns: int, max_chars: int = 300) -> str:
    """
    Lấy N turns gần nhất, truncate mỗi message xuống max_chars.
    Trả về string ngắn gọn để inject vào instructions.
    """
    if not history:
        return ""
    turns = history[-(max_turns * 2):]
    lines = []
    for m in turns:
        role    = "User" if m["role"] == "user" else "AI"
        content = m["content"][:max_chars]
        if len(m["content"]) > max_chars:
            content += "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────────────────────

async def chat(
    query:      str,
    user:       UserPermissionContext,
    session_id: str,
    history:    list[dict],
    app_module: Optional[str] = None,
) -> dict:
    # Lưu context để modata-mcp kiểm tra quyền theo session_id
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    # Instructions ngắn — chỉ thông tin runtime cần thiết
    instructions = [
        f"[session_id:{session_id}] [username:{user.username}] "
        f"[don_vi:{user.don_vi_code}] [company:{user.company_code}]",
    ]

    # Inject history ngắn gọn (max 3 turns, mỗi message max 300 chars)
    history_text = _trim_history(history, max_turns=settings.RAG_MAX_HISTORY)
    if history_text:
        instructions.append(f"Lịch sử:\n{history_text}")

    # Augmented query — chỉ giữ thông tin AI cần để filter
    augmented_query = (
        f"[username:{user.username}] [company:{user.company_code}]\n"
        f"{query}"
    )

    mcp_tools = MCPTools(
        transport="sse",
        url=settings.MCP_GATEWAY_URL,
        refresh_connection=True,
    )
    await mcp_tools.connect()
    logger.info("Agent connected — %d tools", len(mcp_tools.tools) if hasattr(mcp_tools, "tools") else "?")

    try:
        agent = Agent(
            model=OpenAILike(
                id=settings.LLM_MODEL,
                api_key=settings.LLM_API_KEY or "none",
                base_url=_get_llm_base_url(),
                max_tokens=settings.LLM_MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                request_params={
                    "tool_choice": "auto",
                    "extra_body": {
                        "enable_thinking": settings.LLM_ENABLE_THINKING,
                        "stream": False,
                    },
                },
            ),
            tools=[mcp_tools],
            session_id=session_id,
            user_id=user.user_id,
            description=SYSTEM_PROMPT,
            instructions=instructions,
            markdown=False,
            # 0 = Agno không tự inject history — đã tự inject ở trên
            num_history_messages=0,
        )

        response = await agent.arun(augmented_query)
        answer   = response.content if hasattr(response, "content") else str(response)

    finally:
        await mcp_tools.close()

    # Lưu lịch sử — giữ đủ để trim về RAG_MAX_HISTORY khi cần
    updated = history + [
        {"role": "user",      "content": query},
        {"role": "assistant", "content": answer},
    ]
    # Lưu tối đa 20 turns trong DB, nhưng chỉ inject 3 turns vào context
    updated = updated[-40:]
    session_store.save(session_id, user.user_id, user.username, updated)

    return {
        "session_id": session_id,
        "answer":     answer,
        "sources":    [],
    }