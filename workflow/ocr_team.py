"""
workflow/ocr_team.py  (v1 — OCR Pipeline 3 bước tuần tự)

Pipeline:
  Bước 1  — trích xuất text fields từ OCR  (không tool, không MCP)
  Bước 2a — tìm nhân viên qua hrm_search_employees  (MCP)
  Bước 2b — tìm phòng ban qua tools_get_org_tree     (MCP)
  Bước 3  — lắp ghép JSON cuối                       (không tool)

Bước 2a + 2b chạy tuần tự (KHÔNG dùng asyncio.gather) để tránh
lỗi MCP SSE cancel-scope cross-task khi dùng anyio.

Expose:
  process_ocr_document(ocr_text, session_id, user) → JSON string
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

from agno.agent import Agent
from agno.tools.mcp import MCPTools

from utils.qwen_model import QwenOpenAILike as OpenAILike
from app.core.config import settings
from utils.permission import UserPermissionContext
from workflow.session import session_store

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# AGENT IDs
# ─────────────────────────────────────────────────────────────
AGENT_ID_OCR_STEP1  = "ocr-step1-extract"
AGENT_ID_OCR_STEP2A = "ocr-step2a-employees"
AGENT_ID_OCR_STEP2B = "ocr-step2b-orgtree"
AGENT_ID_OCR_STEP3  = "ocr-step3-assemble"

# ─────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────

OCR_STEP1_PROMPT = """\
Bạn là trợ lý trích xuất văn bản. NHIỆM VỤ DUY NHẤT: đọc văn bản OCR và trích xuất thông tin.
TUYỆT ĐỐI KHÔNG gọi bất kỳ tool nào. Chỉ đọc và điền JSON.

QUY TẮC XÁC ĐỊNH:
- ten_nguoi_lap   : người ký ở mục có số thứ tự CAO NHẤT (III > II > I), hoặc người thuộc
                    "PHẦN GHI DÀNH CHO PHÒNG BAN TRÌNH". Lấy tên đầy đủ có dấu.
- ten_lanh_dao    : người ký ở mục I hoặc II có chức vụ quản lý
                    (ưu tiên: Quản trị hệ thống > Kế toán). Lấy tên đầy đủ có dấu.
- ten_phong_ban   : phòng ban đứng ra trình (VD: "Phòng phát triển sản phẩm", "Khối kỹ thuật",
                    "Trung tâm CNTT"). Lấy từ nội dung hoặc chữ ký cuối.
- ngay_lap_to_trinh : định dạng ISO 8601 (VD: "2026-04-02T00:00:00.000+07:00")
- nam_ghi_nhan    : số nguyên năm (VD: 2026)

Trả về JSON thuần, không markdown, không giải thích:
{
    "ten_tai_lieu": "<tiêu đề đầy đủ>",
    "tieu_de": "<V/v hoặc dòng tiêu đề chính, null nếu không có>",
    "tom_tat": "<tóm tắt 1-2 câu>",
    "nam_ghi_nhan": <number>,
    "ma_ho_so": "<mã hồ sơ hoặc null>",
    "so_hieu_to_trinh": "<số tờ trình>",
    "ngay_lap_to_trinh": "<ISO 8601>",
    "loai_to_trinh": "<loại tờ trình>",
    "noi_dung_to_trinh": "<nội dung chính>",
    "so_luong_luu_ban_cung": <number hoặc null>,
    "ghi_chu": "<ghi chú hoặc null>",
    "ten_nguoi_lap": "<họ tên đầy đủ có dấu>",
    "ten_lanh_dao": "<họ tên đầy đủ có dấu>",
    "ten_phong_ban": "<tên phòng ban trình>"
}
"""

OCR_STEP2A_PROMPT = """\
Bạn là trợ lý tra cứu nhân viên. Dùng tool hrm_search_employees để tìm thông tin nhân viên.

NHIỆM VỤ: Tìm kiếm 2 nhân viên theo tên thực được cung cấp trong TÊN NGƯỜI LẬP và TÊN LÃNH ĐẠO.
- Gọi hrm_search_employees ĐÚNG 2 LẦN: 1 lần cho người lập, 1 lần cho lãnh đạo.
- TUYỆT ĐỐI KHÔNG gọi lại tool sau khi đã có đủ 2 kết quả. Trả JSON ngay lập tức.
- <tên thực> là họ tên đầy đủ của người, KHÔNG phải nhãn "Người lập" hay "Lãnh đạo phê duyệt".
- Lấy chính xác từ kết quả: _id, ten_dang_nhap, email, phong_cap_1, phong_ban_phu_trach,
  ho, ten, ten_email.
- Ghép ho và ten thành ho_va_ten.
- KHÔNG tự đoán bất kỳ giá trị nào không có trong kết quả tool.

Trả về JSON thuần:
{
    "nguoi_lap": {
        "_id": "<từ tool hoặc null>",
        "ten_dang_nhap": "<từ tool hoặc null>",
        "email": "<từ tool hoặc null>",
        "ho_va_ten": "<từ tool ghép từ ho và ten hoặc null>",
        "ten_email": "<từ tool hoặc null>",
        "phong_cap_1": {
            "_id": "<từ tool hoặc null>",
            "label": "<từ tool hoặc null>", 
            "value": "<từ tool hoặc null>"
        },
        "phong_ban_phu_trach": {
            "_id": "<từ tool hoặc null>",
            "label": "<từ tool hoặc null>",
            "value": "<từ tool hoặc null>"
        }
    },
    "lanh_dao": {
        "_id": "<từ tool hoặc null>",
        "ten_dang_nhap": "<từ tool hoặc null>",
        "email": "<từ tool hoặc null>",
        "ho_va_ten": "<từ tool ghép từ ho và ten hoặc null>",
        "ten_email": "<từ tool hoặc null>",
        "phong_cap_1": {
            "_id": "<từ tool hoặc null>",
            "label": "<từ tool hoặc null>", 
            "value": "<từ tool hoặc null>"
        },
        "phong_ban_phu_trach": {
            "_id": "<từ tool hoặc null>",
            "label": "<từ tool hoặc null>",
            "value": "<từ tool hoặc null>"
        }
    }
}
Nếu không tìm thấy ai → điền null cho toàn bộ fields của người đó.
"""

OCR_STEP2B_PROMPT = """\
Bạn là trợ lý tra cứu tổ chức. Dùng tool tools_get_org_tree để tìm thông tin phòng ban.

NHIỆM VỤ: Tìm phòng ban theo tên được cung cấp.
- Gọi tools_get_org_tree(session_id=<session_id>, ten_don_vi_to_chuc=<tên phòng ban>).
- Lấy chính xác từ kết quả: _id, code, ten_don_vi_to_chuc.
- KHÔNG tự đoán bất kỳ giá trị nào không có trong kết quả tool.

Trả về JSON thuần:
{
    "phong_ban": {
        "_id": "<từ tool hoặc null>",
        "code": "<từ tool hoặc null>",
        "ten_don_vi_to_chuc": "<từ tool hoặc null>"
    }
}
Nếu không tìm thấy → điền null cho toàn bộ fields.
"""

OCR_STEP3_PROMPT = """\
Bạn là trợ lý lắp ghép dữ liệu. Bạn nhận được 3 khối dữ liệu đã có sẵn và lắp vào JSON cuối.
TUYỆT ĐỐI KHÔNG gọi tool. TUYỆT ĐỐI KHÔNG tự bịa thêm bất kỳ giá trị nào.
Chỉ mapping dữ liệu từ input vào đúng vị trí trong format JSON bên dưới.

Trả về JSON thuần (không markdown, không backtick, bắt đầu { kết thúc }):
{
    "ten_tai_lieu": "<step1.ten_tai_lieu>",
    "tieu_de": "<step1.tieu_de>",
    "tom_tat": "<step1.tom_tat>",
    "nam_ghi_nhan": <step1.nam_ghi_nhan>,
    "so_hieu_to_trinh": "<step1.so_hieu_to_trinh>",
    "ngay_lap_to_trinh": "<step1.ngay_lap_to_trinh>",
    "nguoi_lap_to_trinh": {
        "objectValue": [
            {"key": "ten_dang_nhap", "label": "Tên",   "value": "<step2a.nguoi_lap.ten_dang_nhap>", "is_show": true, "is_save": false},
            {"key": "email",         "label": "Email",  "value": "<step2a.nguoi_lap.email>",         "is_show": true, "is_save": false},
            {"key": "phong_cap_1",   "label": "Khối",
             "value": {
                "_id": "<step2a.nguoi_lap.phong_cap_1._id>",
                "label": "<step2a.nguoi_lap.phong_cap_1.label>",
                "value": "<step2a.nguoi_lap.phong_cap_1.value>",
                "data_source": "danh_muc_don_vi_to_chuc_list",
                "display_member": "ten_don_vi_to_chuc",
                "value_member": "code"
            },
             "is_show": true, "is_save": false},
            {"key": "phong_ban_phu_trach", "label": "Ban",
             "value": {
                 "_id": "<step2a.nguoi_lap.phong_ban_phu_trach._id>",
                 "label": "<step2a.nguoi_lap.phong_ban_phu_trach.label>",
                 "value": "<step2a.nguoi_lap.phong_ban_phu_trach.value>",
                 "data_source": "danh_muc_don_vi_to_chuc_list",
                 "display_member": "ten_don_vi_to_chuc",
                 "value_member": "code"
             },
             "is_show": true, "is_save": false}
        ],
        "option": {"_id": "<step2a.nguoi_lap_to_trinh._id>", "ten_email": "<step2a.nguoi_lap.ten_email>"},
        "label": "<step2a.nguoi_lap.ho_va_ten>",
        "value": "<step2a.nguoi_lap.ten_dang_nhap>"
    },
    "loai_to_trinh": "<step1.loai_to_trinh>",
    "phong_ban": {
        "objectValue": [
            {"key": "code", "label": "Mã", "value": "<step2b.phong_ban.code>", "is_show": true, "is_save": false}
        ],
        "option": {"_id": "<step2b.phong_ban._id>", "ten_don_vi_to_chuc": "<step2b.phong_ban.ten_don_vi_to_chuc>"},
        "label": "<step2b.phong_ban.ten_don_vi_to_chuc>",
        "value": "<step2b.phong_ban.code>"
    },
    "noi_dung_to_trinh": "<step1.noi_dung_to_trinh>",
    "so_luong_luu_ban_cung": <step1.so_luong_luu_ban_cung>,
    "lanh_dao_phe_duyet": {
        "objectValue": [
            {"key": "ten_dang_nhap", "label": "Tên",   "value": "<step2a.lanh_dao.ten_dang_nhap>", "is_show": true, "is_save": false},
            {"key": "email",         "label": "Email",  "value": "<step2a.lanh_dao.email>",         "is_show": true, "is_save": false},
            {"key": "phong_cap_1",   "label": "Khối",
             "value": {
                "_id": "<step2a.lanh_dao.phong_cap_1._id>",
                "label": "<step2a.lanh_dao.phong_cap_1.label>", 
                "value": "<step2a.lanh_dao.phong_cap_1.value>",
                "data_source": "danh_muc_don_vi_to_chuc_list",
                "display_member": "ten_don_vi_to_chuc",
                "value_member": "code"
             },
             "is_show": true, "is_save": false},
            {"key": "phong_ban_phu_trach", "label": "Ban",
             "value": {
                 "_id": "<step2a.lanh_dao.phong_ban_phu_trach._id>",
                 "label": "<step2a.lanh_dao.phong_ban_phu_trach.label>",
                 "value": "<step2a.lanh_dao.phong_ban_phu_trach.value>",
                 "data_source": "danh_muc_don_vi_to_chuc_list",
                 "display_member": "ten_don_vi_to_chuc",
                 "value_member": "code"
             },
             "is_show": true, "is_save": false}
        ],
        "option": {"_id": "<step2a.lanh_dao._id>", "ten_email": "<step2a.lanh_dao.ten_email>"},
        "label": "<step2a.lanh_dao.ho_va_ten>",
        "value": "<step2a.lanh_dao.ten_dang_nhap>"
    },
    "ghi_chu": "<step1.ghi_chu>"
}
"""

# ─────────────────────────────────────────────────────────────
# MODEL FACTORY
# ─────────────────────────────────────────────────────────────

def _get_llm_base_url() -> str:
    url = settings.LLM_BASE_URL.rstrip("/")
    return url if url.endswith("/v1") else f"{url}/v1"


def _make_ocr_model(tool_choice: str = "none") -> OpenAILike:
    """
    Model cho OCR pipeline.
    tool_choice="none"     → step1, step3 (không được gọi tool)
    tool_choice="required" → step2a, step2b (bắt buộc gọi tool)
    """
    return OpenAILike(
        id=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "none",
        base_url=_get_llm_base_url(),
        temperature=0.0,
        request_params={
            "tool_choice": tool_choice,
            "extra_body": {
                "enable_thinking": False,
                "thinking_budget": 0,
                "stream": False,
            },
        },
    )


# ─────────────────────────────────────────────────────────────
# AGENT FACTORY — tạo mới mỗi request để tránh cancel-scope
# cross-task của MCP SSE client
# ─────────────────────────────────────────────────────────────

def _make_ocr_agents(session_id: str, username: str) -> dict[str, Agent]:
    """
    Tạo 4 agents mới cho mỗi OCR request.
    MCPTools được khởi tạo fresh để tránh lỗi anyio cancel-scope
    khi cùng MCP connection được dùng lại qua nhiều asyncio tasks.
    """
    session_ctx = [
        f'session_id = "{session_id}"',
        f'username = "{username}"',
        "Dùng đúng session_id trên khi gọi tool.",
    ]

    # MCPTools tạo mới — mỗi agent dùng connection riêng
    mcp_2a = MCPTools(url=settings.MCP_GATEWAY_URL, transport="sse")
    mcp_2b = MCPTools(url=settings.MCP_GATEWAY_URL, transport="sse")

    common = dict(
        add_history_to_context=False,
        add_datetime_to_context=False,
        markdown=False,
        # Không lưu run vào storage → mỗi lần arun() là 1 fresh turn,
        # tránh agent replay tool-call từ lần chạy trước khi dùng lại cùng session_id
        search_session_history=False,
    )

    return {
        AGENT_ID_OCR_STEP1: Agent(
            id=AGENT_ID_OCR_STEP1,
            name="OCR Step1 — Extract Text",
            model=_make_ocr_model(tool_choice="none"),
            instructions=[OCR_STEP1_PROMPT],
            tools=[],
            **common,
        ),
        AGENT_ID_OCR_STEP2A: Agent(
            id=AGENT_ID_OCR_STEP2A,
            name="OCR Step2a — Search Employees",
            # "auto" thay vì "required": tránh model bị ép gọi lại tool
            # sau khi đã có đủ kết quả trong context để trả JSON
            model=_make_ocr_model(tool_choice="auto"),
            instructions=session_ctx + [OCR_STEP2A_PROMPT],
            tools=[mcp_2a],
            **common,
        ),
        AGENT_ID_OCR_STEP2B: Agent(
            id=AGENT_ID_OCR_STEP2B,
            name="OCR Step2b — Search OrgTree",
            model=_make_ocr_model(tool_choice="auto"),
            instructions=session_ctx + [OCR_STEP2B_PROMPT],
            tools=[mcp_2b],
            **common,
        ),
        AGENT_ID_OCR_STEP3: Agent(
            id=AGENT_ID_OCR_STEP3,
            name="OCR Step3 — Assemble JSON",
            model=_make_ocr_model(tool_choice="none"),
            instructions=[OCR_STEP3_PROMPT],
            tools=[],
            **common,
        ),
    }


# ─────────────────────────────────────────────────────────────
# JSON HELPER
# ─────────────────────────────────────────────────────────────

def _parse_json_response(response: Any) -> dict:
    """Trích JSON từ agent response — xử lý cả string và list content block."""
    raw = ""
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, list):
            raw = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        else:
            raw = str(content or "")
    else:
        raw = str(response)

    raw = raw.strip()
    # Bóc backtick nếu model quên quy tắc
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                pass

    logger.warning("OCR: không parse được JSON | preview=%s", raw[:300])
    return {}


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

async def process_ocr_document(
    ocr_text:   str,
    session_id: str,
    user:       UserPermissionContext,
) -> str:
    """
    Chạy OCR pipeline 3 bước tuần tự.
    Trả về JSON string hoàn chỉnh.

    Lưu ý: bước 2a và 2b chạy TUẦN TỰ (không gather) để tránh
    lỗi MCP SSE cancel-scope cross-task của anyio.
    """
    t0 = time.time()
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    agents = _make_ocr_agents(session_id, user.username)

    # ── BƯỚC 1: trích xuất text ───────────────────────────────────────────────
    logger.info("OCR step1 start | session=%s", session_id)
    resp1  = await agents[AGENT_ID_OCR_STEP1].arun(
        f"Văn bản OCR cần xử lý:\n\n{ocr_text}",
        session_id=session_id,
        user_id=user.user_id,
    )
    step1  = _parse_json_response(resp1)
    logger.info("OCR step1 done | nguoi_lap=%s lanh_dao=%s phong_ban=%s",
                step1.get("ten_nguoi_lap"), step1.get("ten_lanh_dao"), step1.get("ten_phong_ban"))

    ten_nguoi_lap = step1.get("ten_nguoi_lap") or ""
    ten_lanh_dao  = step1.get("ten_lanh_dao")  or ""
    ten_phong_ban = step1.get("ten_phong_ban") or ""

    # ── BƯỚC 2a: tìm nhân viên (tuần tự — tránh cancel-scope) ────────────────
    logger.info("OCR step2a start | nguoi_lap=%s lanh_dao=%s", ten_nguoi_lap, ten_lanh_dao)
    resp2a = await agents[AGENT_ID_OCR_STEP2A].arun(
        f"session_id: {session_id}\n"
        f"Gọi hrm_search_employees với keyword=\"{ten_nguoi_lap}\" cho người lập.\n"
        f"Gọi hrm_search_employees với keyword=\"{ten_lanh_dao}\" cho lãnh đạo.\n"
        f"Sau 2 lần gọi tool trên, trả JSON ngay, KHÔNG gọi thêm tool nào nữa.",
        session_id=session_id,
        user_id=user.user_id,
    )
    step2a = _parse_json_response(resp2a)
    logger.info("OCR step2a done | ten_dang_nhap_nguoi_lap=%s",
                (step2a.get("nguoi_lap") or {}).get("ten_dang_nhap"))

    # ── BƯỚC 2b: tìm phòng ban (tuần tự — tránh cancel-scope) ────────────────
    logger.info("OCR step2b start | phong_ban=%s", ten_phong_ban)
    resp2b = await agents[AGENT_ID_OCR_STEP2B].arun(
        f"session_id: {session_id}\n"
        f"Gọi tools_get_org_tree với ten_don_vi_to_chuc=\"{ten_phong_ban}\".\n"
        f"Sau khi có kết quả, trả JSON ngay, KHÔNG gọi thêm tool.",
        session_id=session_id,
        user_id=user.user_id,
    )
    step2b = _parse_json_response(resp2b)
    logger.info("OCR step2b done | code=%s",
                (step2b.get("phong_ban") or {}).get("code"))

    # ── BƯỚC 3: lắp ghép JSON cuối ───────────────────────────────────────────
    logger.info("OCR step3 start | session=%s", session_id)
    resp3  = await agents[AGENT_ID_OCR_STEP3].arun(
        f"Lắp ghép JSON cuối từ 3 khối dữ liệu:\n\n"
        f"=== STEP1 ===\n{json.dumps(step1,  ensure_ascii=False)}\n\n"
        f"=== STEP2A ===\n{json.dumps(step2a, ensure_ascii=False)}\n\n"
        f"=== STEP2B ===\n{json.dumps(step2b, ensure_ascii=False)}",
        session_id=session_id,
        user_id=user.user_id,
    )
    step3  = _parse_json_response(resp3)
    logger.info("OCR pipeline done | %.2fs | session=%s", time.time() - t0, session_id)

    return json.dumps(step3, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────
# SSE STREAMING API
# ─────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """Tạo SSE frame chuẩn."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_ocr_document(
    ocr_text:   str,
    session_id: str,
    user:       UserPermissionContext,
) -> AsyncGenerator[str, None]:
    """
    Chạy OCR pipeline 3 bước và yield SSE events liên tục.

    Events:
      progress  — {"step": int, "total": 4, "message": str}
      result    — {"step": int, "data": dict}          (kết quả từng bước)
      done      — {"session_id": str, "result": dict, "raw": str,
                   "pipeline": str, "metrics": {"total_duration": float}}
      error     — {"message": str, "step": int}
    """
    t0 = time.time()
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )

    agents = _make_ocr_agents(session_id, user.username)

    # ── BƯỚC 1 ────────────────────────────────────────────────
    yield _sse("progress", {"step": 1, "total": 4, "message": "Đang trích xuất thông tin từ văn bản OCR..."})
    try:
        logger.info("OCR stream step1 start | session=%s", session_id)
        resp1 = await agents[AGENT_ID_OCR_STEP1].arun(
            f"Văn bản OCR cần xử lý:\n\n{ocr_text}",
            session_id=session_id,
            user_id=user.user_id,
        )
        step1 = _parse_json_response(resp1)
        logger.info("OCR stream step1 done | nguoi_lap=%s lanh_dao=%s",
                    step1.get("ten_nguoi_lap"), step1.get("ten_lanh_dao"))
        yield _sse("result", {"step": 1, "data": {
            "ten_nguoi_lap": step1.get("ten_nguoi_lap"),
            "ten_lanh_dao":  step1.get("ten_lanh_dao"),
            "ten_phong_ban": step1.get("ten_phong_ban"),
        }})
    except Exception as e:
        logger.error("OCR stream step1 error: %s", e, exc_info=True)
        yield _sse("error", {"message": f"Bước 1 thất bại: {e}", "step": 1})
        return

    ten_nguoi_lap = step1.get("ten_nguoi_lap") or ""
    ten_lanh_dao  = step1.get("ten_lanh_dao")  or ""
    ten_phong_ban = step1.get("ten_phong_ban") or ""

    # ── BƯỚC 2a ───────────────────────────────────────────────
    yield _sse("progress", {"step": 2, "total": 4,
               "message": f"Đang tra cứu nhân viên: {ten_nguoi_lap}, {ten_lanh_dao}..."})
    try:
        logger.info("OCR stream step2a start | nguoi_lap=%s lanh_dao=%s",
                    ten_nguoi_lap, ten_lanh_dao)
        resp2a = await agents[AGENT_ID_OCR_STEP2A].arun(
            f"session_id: {session_id}\n"
            f"Gọi hrm_search_employees với keyword=\"{ten_nguoi_lap}\" cho người lập.\n"
            f"Gọi hrm_search_employees với keyword=\"{ten_lanh_dao}\" cho lãnh đạo.\n"
            f"Sau 2 lần gọi tool trên, trả JSON ngay, KHÔNG gọi thêm tool nào nữa.",
            session_id=session_id,
            user_id=user.user_id,
        )
        step2a = _parse_json_response(resp2a)
        nguoi_lap_username = (step2a.get("nguoi_lap") or {}).get("ten_dang_nhap")
        lanh_dao_username  = (step2a.get("lanh_dao")  or {}).get("ten_dang_nhap")
        logger.info("OCR stream step2a done | nguoi_lap=%s lanh_dao=%s",
                    nguoi_lap_username, lanh_dao_username)
        yield _sse("result", {"step": 2, "data": {
            "nguoi_lap_username": nguoi_lap_username,
            "lanh_dao_username":  lanh_dao_username,
        }})
    except Exception as e:
        logger.error("OCR stream step2a error: %s", e, exc_info=True)
        yield _sse("error", {"message": f"Bước 2a thất bại: {e}", "step": 2})
        return

    # ── BƯỚC 2b ───────────────────────────────────────────────
    yield _sse("progress", {"step": 3, "total": 4,
               "message": f"Đang tra cứu phòng ban: {ten_phong_ban}..."})
    try:
        logger.info("OCR stream step2b start | phong_ban=%s", ten_phong_ban)
        resp2b = await agents[AGENT_ID_OCR_STEP2B].arun(
            f"session_id: {session_id}\n"
            f"Gọi tools_get_org_tree với ten_don_vi_to_chuc=\"{ten_phong_ban}\".\n"
            f"Sau khi có kết quả, trả JSON ngay, KHÔNG gọi thêm tool.",
            session_id=session_id,
            user_id=user.user_id,
        )
        step2b = _parse_json_response(resp2b)
        logger.info("OCR stream step2b done | code=%s",
                    (step2b.get("phong_ban") or {}).get("code"))
        yield _sse("result", {"step": 3, "data": {
            "phong_ban_code": (step2b.get("phong_ban") or {}).get("code"),
            "phong_ban_name": (step2b.get("phong_ban") or {}).get("ten_don_vi_to_chuc"),
        }})
    except Exception as e:
        logger.error("OCR stream step2b error: %s", e, exc_info=True)
        yield _sse("error", {"message": f"Bước 2b thất bại: {e}", "step": 3})
        return

    # ── BƯỚC 3 ────────────────────────────────────────────────
    yield _sse("progress", {"step": 4, "total": 4, "message": "Đang lắp ghép kết quả cuối..."})
    try:
        logger.info("OCR stream step3 start | session=%s", session_id)
        resp3 = await agents[AGENT_ID_OCR_STEP3].arun(
            f"Lắp ghép JSON cuối từ 3 khối dữ liệu:\n\n"
            f"=== STEP1 ===\n{json.dumps(step1,  ensure_ascii=False)}\n\n"
            f"=== STEP2A ===\n{json.dumps(step2a, ensure_ascii=False)}\n\n"
            f"=== STEP2B ===\n{json.dumps(step2b, ensure_ascii=False)}",
            session_id=session_id,
            user_id=user.user_id,
        )
        step3 = _parse_json_response(resp3)
        raw   = json.dumps(step3, ensure_ascii=False, indent=2)
        duration = round(time.time() - t0, 3)
        logger.info("OCR stream pipeline done | %.2fs | session=%s", duration, session_id)
        yield _sse("done", {
            "session_id": session_id,
            "result":     step3,
            "raw":        raw,
            "pipeline":   "ocr-pipeline-3step",
            "metrics":    {"total_duration": duration},
        })
    except Exception as e:
        logger.error("OCR stream step3 error: %s", e, exc_info=True)
        yield _sse("error", {"message": f"Bước 3 thất bại: {e}", "step": 4})