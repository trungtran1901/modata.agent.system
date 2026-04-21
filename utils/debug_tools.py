"""
utils/debug_tools.py

Debug utilities để log chi tiết tool calls và errors
"""
import logging
from typing import Any, Optional
from agno.agent import Agent as AgnoAgent

logger = logging.getLogger(__name__)

# Store original invoke
_original_agent_invoke = AgnoAgent.invoke if hasattr(AgnoAgent, 'invoke') else None


def patch_agent_tool_debug():
    """Patch Agent để log chi tiết khi tool call fail"""
    try:
        # Wrap tool execution để log errors
        original_get_tool = AgnoAgent.get_tool
        
        def patched_get_tool(self, tool_name: str) -> Optional[Any]:
            result = original_get_tool(self, tool_name)
            if result is None:
                # Log all available tools
                available = []
                if hasattr(self, 'tools') and self.tools:
                    for tool in self.tools:
                        if hasattr(tool, 'get_tools'):
                            try:
                                tool_list = tool.get_tools()
                                for t in tool_list:
                                    t_name = t.get('name', 'N/A')
                                    available.append(t_name)
                                    logger.debug(f"  Available: {t_name}")
                            except Exception as te:
                                logger.debug(f"  Error listing tools: {te}")
                
                logger.error(
                    f"❌ TOOL NOT FOUND: '{tool_name}'\n"
                    f"   Agent: {self.id or 'unknown'}\n"
                    f"   Available tools ({len(available)}): {', '.join(available[:10])}"
                    f"{'...' if len(available) > 10 else ''}"
                )
            return result
        
        AgnoAgent.get_tool = patched_get_tool
        logger.info("✓ Patched Agent.get_tool for better error logging")
    except Exception as e:
        logger.warning(f"Could not patch Agent.get_tool: {e}")


def log_tool_call_attempt(tool_name: str, arguments: dict, agent_id: str = None):
    """Log khi có tool call attempt"""
    logger.info(
        f"🔧 Tool call: {agent_id or 'unknown'} → {tool_name}\n"
        f"   Args: {arguments}"
    )


if __name__ == "__main__":
    patch_agent_tool_debug()

