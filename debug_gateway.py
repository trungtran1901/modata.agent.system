# test_single_agent.py
import asyncio
from agno.agent import Agent
from agno.models.openai.like import OpenAILike
from agno.tools.mcp import MCPTools
from app.core.config import settings

async def main():
    async with MCPTools(url=settings.MCP_GATEWAY_URL, transport="sse") as mcp_tools:
        print(f"Functions loaded: {len(mcp_tools.functions)}")
        print(list(mcp_tools.functions.keys())[:5])

        agent = Agent(
            model=OpenAILike(
                id=settings.LLM_MODEL,
                api_key=settings.LLM_API_KEY or "none",
                base_url=settings.LLM_BASE_URL,
            ),
            instructions=[
                'session_id = "test-session-123"',
                'username = "tuannv1"',
            ],
            tools=[mcp_tools],
            markdown=False,
        )
        
        response = await agent.arun("thông tin của tôi")
        print(response.content)

asyncio.run(main())