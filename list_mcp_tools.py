#!/usr/bin/env python3
"""
list_mcp_tools.py - List available MCP tools from gateway
Run: python list_mcp_tools.py
"""

import asyncio
import json
from typing import Any, Dict, List

from agno.tools.mcp import MCPTools

from app.core.config import settings


async def list_mcp_tools_sse() -> None:
    """List all MCP tools via httpx transport (recommended)"""
    print("\n" + "="*70)
    print("MCP Tools Available")
    print("="*70)
    
    try:
        # Use httpx transport instead of deprecated SSE
        # Extract base URL (without /sse suffix)
        base_url = settings.MCP_GATEWAY_URL.rstrip("/")
        if base_url.endswith("/sse"):
            base_url = base_url.replace("/sse", "")
        
        print(f"\n[1/3] Initializing MCPTools with httpx transport...")
        print(f"      Gateway URL: {base_url}")
        
        # Initialize with httpx transport (modern, recommended)
        mcp_tools = MCPTools(url=base_url, transport="httpx")
        
        print(f"[2/3] Connecting to MCP Gateway...")
        await mcp_tools.connect()
        
        print(f"[3/3] Fetching tools list...\n")
        
        # Get functions (tools)
        if hasattr(mcp_tools, "functions"):
            functions = mcp_tools.functions
            print(f"✓ Found {len(functions)} functions (tools):\n")
            
            if len(functions) > 0:
                for i, func in enumerate(functions, 1):
                    print(f"  {i}. {func.name}")
                    if hasattr(func, "description") and func.description:
                        print(f"     Description: {func.description}")
                    # Show parameters
                    if hasattr(func, "parameters"):
                        print(f"     Parameters: {func.parameters}")
                    print()
            else:
                print("⚠ No functions found - MCP Gateway may not have tools registered")
        
        # Also check tools attribute
        if hasattr(mcp_tools, "tools"):
            tools_list = mcp_tools.tools
            print(f"\n✓ Tools attribute: {len(tools_list)} items")
            for tool in tools_list[:5]:  # Show first 5
                print(f"  - {tool}")
                
        await mcp_tools.close()
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()


async def query_mcp_tool(tool_name: str, params: Dict[str, Any]) -> None:
    """Query a specific MCP tool"""
    print(f"\n{'='*70}")
    print(f"Querying Tool: {tool_name}")
    print(f"{'='*70}")
    
    try:
        mcp_tools = MCPTools(url=settings.MCP_GATEWAY_URL, transport="sse")
        
        # Try to call the tool
        # Note: This depends on MCPTools implementation
        if hasattr(mcp_tools, "call"):
            result = await mcp_tools.call(tool_name, **params)
            print(f"\n✓ Result:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print("⚠ MCPTools doesn't have call method")
            print(f"Available methods: {[m for m in dir(mcp_tools) if not m.startswith('_')]}")
            
    except Exception as e:
        print(f"✗ Error calling tool: {e}")
        import traceback
        traceback.print_exc()


async def test_hrm_tools() -> None:
    """Test HRM namespace tools"""
    print("\n" + "="*70)
    print("Testing HRM Tools")
    print("="*70)
    
    test_cases = [
        {
            "tool": "hrm_get_employee_info",
            "params": {
                "session_id": "test-session-123",
                "username_or_name": "tuannv1",
            }
        },
        {
            "tool": "hrm_search_employees",
            "params": {
                "session_id": "test-session-123",
                "keyword": "nguyễn",
            }
        },
    ]
    
    for test_case in test_cases:
        try:
            print(f"\n[Testing] {test_case['tool']}")
            await query_mcp_tool(test_case["tool"], test_case["params"])
        except Exception as e:
            print(f"✗ Test failed: {e}")


async def test_hrm_req_tools() -> None:
    """Test HRM_REQ namespace tools"""
    print("\n" + "="*70)
    print("Testing HRM_REQ Tools")
    print("="*70)
    
    test_cases = [
        {
            "tool": "hrm_req_list_requests",
            "params": {
                "session_id": "test-session-123",
                "username": "tuannv1",
                "loai_don": "Đi muộn, về sớm",
                "from_date": "2026-04-01",
                "to_date": "2026-04-30",
            }
        },
        {
            "tool": "hrm_req_get_my_requests",
            "params": {
                "session_id": "test-session-123",
                "username": "tuannv1",
                "loai_don": None,
                "limit": 10,
            }
        },
    ]
    
    for test_case in test_cases:
        try:
            print(f"\n[Testing] {test_case['tool']}")
            await query_mcp_tool(test_case["tool"], test_case["params"])
        except Exception as e:
            print(f"✗ Test failed: {e}")


async def main():
    """Main function"""
    import sys
    
    print("\n" + "="*70)
    print(f"MCP Gateway: {settings.MCP_GATEWAY_URL}")
    print(f"LLM Model: {settings.LLM_MODEL}")
    print("="*70)
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "list":
            await list_mcp_tools_sse()
        elif command == "hrm":
            await test_hrm_tools()
        elif command == "hrm_req":
            await test_hrm_req_tools()
        elif command == "all":
            await list_mcp_tools_sse()
            await test_hrm_tools()
            await test_hrm_req_tools()
        else:
            print(f"Unknown command: {command}")
            print("\nUsage:")
            print("  python list_mcp_tools.py list      # List all tools")
            print("  python list_mcp_tools.py hrm        # Test HRM tools")
            print("  python list_mcp_tools.py hrm_req    # Test HRM_REQ tools")
            print("  python list_mcp_tools.py all        # Test all")
    else:
        # Default: list tools
        await list_mcp_tools_sse()
        print("\n" + "="*70)
        print("Usage:")
        print("  python list_mcp_tools.py list      # List all tools")
        print("  python list_mcp_tools.py hrm        # Test HRM tools")
        print("  python list_mcp_tools.py hrm_req    # Test HRM_REQ tools")
        print("  python list_mcp_tools.py all        # Test all")
        print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
