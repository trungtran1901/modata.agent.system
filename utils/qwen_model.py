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

import openai

from agno.models.openai.like import OpenAILike
from agno.models.response import ModelResponse

logger = logging.getLogger(__name__)

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


# ──────────────────────────────────────────────────────────────
# Monkey-patch OpenAI client to remove stream_options when stream=False
# ──────────────────────────────────────────────────────────────

_original_post = None

def _patched_post(self, *args: Any, **kwargs: Any) -> Any:
    """Patch POST to remove stream_options when stream=False."""
    # Check if this is a chat completion request
    if "stream_options" in kwargs.get("json", {}):
        json_data = kwargs["json"]
        stream = json_data.get("stream", False)
        
        # vLLM doesn't allow stream_options when stream=False
        if not stream:
            logger.debug("Removing stream_options from request (stream=False)")
            json_data.pop("stream_options", None)
    
    return _original_post(self, *args, **kwargs)


# Patch the OpenAI client's post method
try:
    from openai._base_client import BaseClient
    _original_post = BaseClient.post
    BaseClient.post = _patched_post
    logger.info("✓ Patched OpenAI client to remove stream_options when stream=False")
except Exception as e:
    logger.warning("Failed to patch OpenAI client: %s", e)


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
        except json.JSONDecodeError as e:
            logger.warning("QwenModel: cannot parse tool_call JSON: %.200s | Error: %s", raw, e)
            continue

        name = parsed.get("name") or parsed.get("function", {}).get("name")
        args = parsed.get("arguments") or parsed.get("parameters") or {}
        if not name:
            logger.warning("QwenModel: tool_call missing name: %s", parsed)
            continue

        args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)
        tool_obj = _ToolCallObj(
            id=f"call_{uuid.uuid4().hex[:8]}",
            name=name,
            arguments=args_str,
        )
        logger.debug(f"QwenModel: extracted tool_call: name={name}, args={args_str[:100]}")
        tool_calls.append(tool_obj)

    clean = _TOOL_CALL_RE.sub("", content).strip() or None
    return tool_calls, clean


# ──────────────────────────────────────────────────────────────
# Main patched model class
# ──────────────────────────────────────────────────────────────

class QwenOpenAILike(OpenAILike):
    """
    OpenAILike với patch cho Qwen3 text-format tool calls (Agno 2.5.10).

    Vấn đề 1 - Text-format tool calls:
        Qwen3 trả về:
            finish_reason = "stop"
            content       = "<tool_call>{...}</tool_call>"
            tool_calls    = None

        Agno thấy tool_calls=None → bỏ qua, trả content text ra cho user.

    Fix 1:
        Override _parse_provider_response: phát hiện <tool_call> trong content
        → inject _ToolCallObj list + set finish_reason="tool_calls"
        → super() xử lý bình thường, Agno thực thi tool.

    Vấn đề 2 - stream_options validation:
        vLLM reject: "Stream options can only be defined when stream=True"
        Agno gửi stream_options ngay cả khi stream=False.

    Fix 2:
        Monkey-patch OpenAI client's POST method to remove stream_options 
        when stream=False (done at module level above).
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
                        "✅ QwenModel: injecting %d tool_call(s) from text response",
                        len(tool_call_objs),
                    )
                    for tc in tool_call_objs:
                        logger.info(f"   - tool: {tc.name} (id={tc.id})")
                    message.tool_calls   = tool_call_objs
                    message.content      = clean_content
                    choice.finish_reason = "tool_calls"
                else:
                    logger.debug("QwenModel: no valid tool_calls found in response")

        except Exception:
            logger.exception("QwenModel: error in _parse_provider_response patch")

        return super()._parse_provider_response(response, response_format)