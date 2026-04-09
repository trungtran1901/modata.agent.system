"""
utils/qwen_tool_patch.py

Patch cho model Qwen3 (và các LLM dùng <tool_call>...</tool_call> text format
thay vì OpenAI structured tool_calls).

Cách dùng — thay thế _make_model() trong hrm_team.py và hrm_analytics_team.py:

    from utils.qwen_tool_patch import make_qwen_model
    # thay vì: return OpenAILike(...)
    return make_qwen_model(max_tokens=512)

Không cần sửa bất kỳ logic nào khác.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from agno.models.openai.like import OpenAILike

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# REGEX: match <tool_call>{ ... }</tool_call>  (có thể multiline)
# ──────────────────────────────────────────────────────────────
_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def _parse_tool_calls_from_text(text: str) -> list[dict] | None:
    """
    Tìm tất cả <tool_call>{...}</tool_call> trong text.
    Trả về list OpenAI-compatible tool_calls, hoặc None nếu không có.
    """
    matches = _TOOL_CALL_RE.findall(text)
    if not matches:
        return None

    tool_calls = []
    for raw_json in matches:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.warning("Qwen tool_call JSON parse failed: %s", raw_json[:200])
            continue

        name = parsed.get("name") or parsed.get("function", {}).get("name")
        arguments = parsed.get("arguments") or parsed.get("parameters") or {}

        if not name:
            continue

        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        })

    return tool_calls if tool_calls else None


class QwenOpenAILike(OpenAILike):
    """
    OpenAILike với khả năng tự parse <tool_call> text format của Qwen3.

    Qwen3 đôi khi trả về tool call dưới dạng text:
        <tool_call>{"name": "foo", "arguments": {...}}</tool_call>
    thay vì OpenAI structured tool_calls.

    Class này intercept response và convert về đúng format trước khi
    Agno framework xử lý — không cần thay đổi agent/workflow code.
    """

    def _patch_response(self, response: Any) -> Any:
        """
        Kiểm tra response.choices[0].message:
        - Nếu content chứa <tool_call> → chuyển sang tool_calls
        - Nếu đã có tool_calls → giữ nguyên
        """
        try:
            choices = getattr(response, "choices", None)
            if not choices:
                return response

            choice = choices[0]
            message = getattr(choice, "message", None)
            if message is None:
                return response

            # Đã có tool_calls chuẩn → không cần patch
            existing_tool_calls = getattr(message, "tool_calls", None)
            if existing_tool_calls:
                return response

            content = getattr(message, "content", "") or ""
            parsed_calls = _parse_tool_calls_from_text(content)

            if parsed_calls:
                logger.info(
                    "QwenPatch: detected %d tool_call(s) in text, converting...",
                    len(parsed_calls),
                )
                # Build OpenAI-like tool_call objects
                # Agno đọc message.tool_calls là list of object có .id .type .function
                tool_call_objects = [_DictObj(tc) for tc in parsed_calls]

                # Xoá <tool_call> khỏi content để Agno không in ra cho user
                clean_content = _TOOL_CALL_RE.sub("", content).strip() or None

                # Patch message
                try:
                    message.tool_calls = tool_call_objects
                    message.content = clean_content
                    # finish_reason phải là "tool_calls" để Agno tiếp tục loop
                    choice.finish_reason = "tool_calls"
                except AttributeError:
                    # Nếu object immutable → tạo lại bằng dict
                    response = _patch_via_dict(response, parsed_calls, clean_content)

        except Exception:
            logger.exception("QwenPatch: unexpected error, returning original response")

        return response

    # ── Override điểm Agno gọi LLM ────────────────────────────

    def invoke(self, *args, **kwargs):
        response = super().invoke(*args, **kwargs)
        return self._patch_response(response)

    async def ainvoke(self, *args, **kwargs):
        response = await super().ainvoke(*args, **kwargs)
        return self._patch_response(response)


# ──────────────────────────────────────────────────────────────
# Helper: object giả lập để Agno đọc .attribute
# ──────────────────────────────────────────────────────────────

class _DictObj:
    """Chuyển dict thành object có thể truy cập bằng attribute."""

    def __init__(self, d: dict):
        for k, v in d.items():
            if isinstance(v, dict):
                setattr(self, k, _DictObj(v))
            else:
                setattr(self, k, v)

    def __repr__(self):
        return f"_DictObj({self.__dict__})"


def _patch_via_dict(response: Any, tool_calls: list[dict], clean_content: str | None) -> Any:
    """Fallback: patch bằng cách ghi đè __dict__ nếu object không cho phép setattr."""
    try:
        msg = response.choices[0].message
        msg.__dict__.update({
            "tool_calls": [_DictObj(tc) for tc in tool_calls],
            "content": clean_content,
        })
        response.choices[0].__dict__["finish_reason"] = "tool_calls"
    except Exception:
        logger.exception("QwenPatch _patch_via_dict failed")
    return response


# ──────────────────────────────────────────────────────────────
# Factory — thay thế _make_model() trong hrm_team.py
# ──────────────────────────────────────────────────────────────

def make_qwen_model(
    *,
    llm_model: str,
    llm_api_key: str,
    llm_base_url: str,
    max_tokens: int = 512,
) -> QwenOpenAILike:
    """
    Drop-in replacement cho OpenAILike với Qwen3 tool_call patch.

    Ví dụ trong hrm_team.py:

        from utils.qwen_tool_patch import make_qwen_model
        from app.core.config import settings

        def _make_model(max_tokens: int = 512) -> QwenOpenAILike:
            url = settings.LLM_BASE_URL.rstrip("/")
            base_url = url if url.endswith("/v1") else f"{url}/v1"
            return make_qwen_model(
                llm_model=settings.LLM_MODEL,
                llm_api_key=settings.LLM_API_KEY or "none",
                llm_base_url=base_url,
                max_tokens=max_tokens,
            )
    """
    return QwenOpenAILike(
        id=llm_model,
        api_key=llm_api_key,
        base_url=llm_base_url,
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