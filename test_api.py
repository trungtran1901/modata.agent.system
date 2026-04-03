"""test_api.py — Test Agno AgentOS API"""
import asyncio
import httpx
import json

BASE_URL = "http://127.0.0.1:8000"

# Fake token for testing
FAKE_TOKEN = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0X3VzZXIiLCJ1c2VyX2lkIjoidGVzdF91c2VyIn0.fake"

async def test_chat_simple():
    """Test simple chat request."""
    async with httpx.AsyncClient() as client:
        # Simple JSON without trailing comma
        payload = {
            "query": "Hôm nay tôi làm bao nhiêu giờ?",
            "session_id": "test_session_001"
        }
        
        print("📝 Testing simple query...")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = await client.post(
            f"{BASE_URL}/chat",
            json=payload,  # Uses json encoder automatically
            headers={"Authorization": FAKE_TOKEN}
        )
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ Success!")
            print(f"Answer: {data.get('answer')}")
            print(f"Agents used: {data.get('agents_used')}")
        elif response.status_code == 401:
            print("\n❌ Auth error - token invalid (expected)")
        else:
            print(f"\n❌ Error: {response.status_code}")

async def test_chat_with_trailing_comma():
    """Test with malformed JSON (trailing comma) - should fail."""
    async with httpx.AsyncClient() as client:
        # Manually construct malformed JSON
        malformed_json = '{"query": "test", "session_id": "sess1",}'
        
        print("\n📝 Testing with trailing comma (should fail)...")
        print(f"Malformed JSON: {malformed_json}")
        
        response = await client.post(
            f"{BASE_URL}/chat",
            content=malformed_json,
            headers={
                "Authorization": FAKE_TOKEN,
                "Content-Type": "application/json"
            }
        )
        
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    print("🚀 Testing Agno AgentOS API\n")
    
    print("=" * 60)
    asyncio.run(test_chat_simple())
    
    print("\n" + "=" * 60)
    asyncio.run(test_chat_with_trailing_comma())
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")
