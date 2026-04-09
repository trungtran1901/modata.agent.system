"""
utils/qwen_model.py

QwenOpenAILike — drop-in thay thế OpenAILike cho Agno 2.5.10+

Patch _parse_provider_response để xử lý Qwen3 trả tool call dưới dạng
text <tool_call>...</tool_call> thay vì OpenAI structured tool_calls.

Cách dùng — trong hrm_team.py và hrm_analytics_team.py:

    # Thay dòng import:
    from agno.models.openai.like import OpenAILike
    # Bằng:
    from utils.qwen_model import QwenOpenAILike as OpenAILike

    # _make_model() giữ NGUYÊN hoàn toàn — không cần sửa gì thêm.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, Optional, Type, Union

from agno.models.openai.like import OpenAILike
from agno.models.response import ModelResponse

logger = logging.getLogger(__name__)

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


# ──────────────────────────────────────────────────────────────
# Fake tool call objects — Agno gọi t.model_dump() trên mỗi cái
# ──────────────────────────────────────────────────────────────

class _FunctionObj:
    def __init__(self, name: str, arguments: str):
        self.name      = name
        self.arguments = arguments


class _ToolCallObj:
    """
    Giả lập openai.types.chat.ChatCompletionMessageToolCall.
    Agno 2.5.10 gọi: model_response.tool_calls = [t.model_dump() for t in ...]
    """
    def __init__(self, id: str, name: str, arguments: str):
        self.id       = id
        self.type     = "function"
        self.function = _FunctionObj(name=name, arguments=arguments)

    def model_dump(self, **_kwargs) -> dict:
        return {
            "id":   self.id,
            "type": "function",
            "function": {
                "name":      self.function.name,
                "arguments": self.function.arguments,
            },
        }


# ──────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────

def _extract_tool_calls(content: str) -> tuple[list[_ToolCallObj], str | None]:
    raw_matches = _TOOL_CALL_RE.findall(content)
    if not raw_matches:
        return [], content

    tool_calls: list[_ToolCallObj] = []
    for raw in raw_matches:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("QwenModel: cannot parse tool_call JSON: %.200s", raw)
            continue

        name = parsed.get("name") or parsed.get("function", {}).get("name")
        args = parsed.get("arguments") or parsed.get("parameters") or {}
        if not name:
            continue

        args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        tool_calls.append(_ToolCallObj(
            id=f"call_{uuid.uuid4().hex[:8]}",
            name=name,
            arguments=args_str,
        ))

    clean = _TOOL_CALL_RE.sub("", content).strip() or None
    return tool_calls, clean


# ──────────────────────────────────────────────────────────────
# Main patched model class
# ──────────────────────────────────────────────────────────────

class QwenOpenAILike(OpenAILike):
    """
    OpenAILike với patch cho Qwen3 text-format tool calls (Agno 2.5.10).

    Vấn đề:
        Qwen3 trả về:
            finish_reason = "stop"
            content       = "<tool_call>{...}</tool_call>"
            tool_calls    = None

        Agno thấy tool_calls=None → bỏ qua, trả content text ra cho user.

    Fix:
        Override _parse_provider_response: phát hiện <tool_call> trong content
        → inject _ToolCallObj list + set finish_reason="tool_calls"
        → super() xử lý bình thường, Agno thực thi tool.
    """

    def _parse_provider_response(
        self,
        response: Any,
        response_format: Optional[Union[Dict, Type]] = None,
    ) -> ModelResponse:

        try:
            choice  = response.choices[0]
            message = choice.message
            content = message.content or ""

            if not getattr(message, "tool_calls", None) and "<tool_call>" in content:
                tool_call_objs, clean_content = _extract_tool_calls(content)

                if tool_call_objs:
                    logger.info(
                        "QwenModel: injecting %d tool_call(s) parsed from text",
                        len(tool_call_objs),
                    )
                    message.tool_calls   = tool_call_objs
                    message.content      = clean_content
                    choice.finish_reason = "tool_calls"

        except Exception:
            logger.exception("QwenModel: error in _parse_provider_response patch")

        return super()._parse_provider_response(response, response_format)