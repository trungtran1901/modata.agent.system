"""
Debug script để xem chính xác lỗi "Function not found" là gì.

Chạy:
  python debug_agentosagno.py
"""

import asyncio
import httpx
import json
import logging

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

BASE_URL = "http://localhost:8000"
API_KEY = "818eccbf414d45918ec7e196de10737d"

async def test_agentosagno():
    """Test AgentOS endpoint with debugging"""
    
    async with httpx.AsyncClient(timeout=30) as client:
        # Test 1: Direct chat (should work)
        print("\n" + "="*60)
        print("TEST 1: Direct chat (/hitc/chat)")
        print("="*60)
        
        response = await client.post(
            f"{BASE_URL}/hitc/chat",
            headers={
                "X-Api-Key": API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "query": "Get employee data",
                "session_id": "test-direct-001"
            }
        )
        print(f"Status: {response.status_code}")
        try:
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2, ensure_ascii=False)}")
        except:
            print(f"Raw response: {response.text}")
        
        # Test 2: AgentOS endpoint (might fail)
        print("\n" + "="*60)
        print("TEST 2: AgentOS endpoint (/teams/hrm-team/runs)")
        print("="*60)
        
        response = await client.post(
            f"{BASE_URL}/teams/hrm-team/runs",
            headers={
                "X-Api-Key": API_KEY,
                "X-Session-Id": "test-agentosagno-001",
                "Content-Type": "application/json"
            },
            json={
                "message": "Get employee data",
                "session_id": "test-agentosagno-001"
            }
        )
        print(f"Status: {response.status_code}")
        try:
            data = response.json()
            print(f"Response: {json.dumps(data, indent=2, ensure_ascii=False)}")
            
            # Look for "Function not found" error
            response_str = json.dumps(data, ensure_ascii=False).lower()
            if "function not found" in response_str:
                print("\n⚠️ ERROR: 'Function not found' detected in response!")
                print("This means context injection is NOT working.")
        except:
            print(f"Raw response: {response.text[:500]}")

if __name__ == "__main__":
    asyncio.run(test_agentosagno())
