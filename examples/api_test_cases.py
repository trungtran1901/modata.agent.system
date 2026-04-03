"""
examples/api_test_cases.py

Test cases cho Agno AgentOS API với các scenarios khác nhau.
"""
import json
import asyncio
from typing import Optional, Any


def print_section(title: str):
    """Print formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


class APITester:
    """Test helper để test API endpoints."""
    
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url
        self.token = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
    
    def test_json_formatting(self):
        """Test JSON formatting scenarios."""
        print_section("JSON Formatting Test Cases")
        
        test_cases = [
            {
                "name": "✅ Valid JSON",
                "payload": '{"query": "Hôm nay tôi làm bao nhiêu giờ?", "session_id": "s1"}',
                "should_pass": True,
            },
            {
                "name": "❌ Trailing comma in object",
                "payload": '{"query": "test", "session_id": "s1",}',
                "should_pass": False,
            },
            {
                "name": "❌ Trailing comma in array",
                "payload": '{"items": [1, 2, 3,], "query": "test"}',
                "should_pass": False,
            },
            {
                "name": "❌ Missing quotes on key",
                "payload": '{query: "test", "session_id": "s1"}',
                "should_pass": False,
            },
            {
                "name": "❌ Single quotes instead of double",
                "payload": "{'query': 'test', 'session_id': 's1'}",
                "should_pass": False,
            },
            {
                "name": "✅ Unicode in string",
                "payload": '{"query": "Hôm nay tôi làm bao nhiêu giờ?", "session_id": "s1"}',
                "should_pass": True,
            },
        ]
        
        for tc in test_cases:
            print(f"\n{tc['name']}")
            print(f"Payload: {tc['payload']}")
            
            try:
                json.loads(tc['payload'])
                result = "✅ VALID"
            except json.JSONDecodeError as e:
                result = f"❌ INVALID: {e.msg}"
            
            expected = "should pass" if tc['should_pass'] else "should fail"
            print(f"Result: {result} ({expected})")
    
    def get_valid_requests(self) -> dict[str, dict]:
        """Get valid request examples."""
        return {
            "simple_check_in": {
                "query": "Hôm nay tôi làm bao nhiêu giờ?",
                "session_id": "session_001",
            },
            "analytics_query": {
                "query": "Có bao nhiêu nhân viên trong công ty?",
                "session_id": "session_002",
            },
            "employee_info": {
                "query": "Thông tin nhân viên của tôi là gì?",
                "session_id": "session_003",
            },
            "complex_query": {
                "query": "Hôm nay tôi làm bao nhiêu giờ và tính lương của tôi",
                "session_id": "session_004",
            },
        }
    
    def generate_curl_examples(self):
        """Generate curl command examples."""
        print_section("CURL Command Examples")
        
        requests = self.get_valid_requests()
        
        for name, payload in requests.items():
            print(f"\n📝 {name}:")
            
            # Create proper JSON string
            json_str = json.dumps(payload)
            
            # PowerShell-safe curl command
            curl_cmd = (
                f'curl -X POST {self.base_url}/chat '
                f'-H "Authorization: {self.token}" '
                f'-H "Content-Type: application/json" '
                f'-d \'{json_str}\''
            )
            
            print(f"$ {curl_cmd}")
    
    def generate_python_examples(self):
        """Generate Python client examples."""
        print_section("Python Client Examples")
        
        code = '''
import asyncio
import httpx
import json

async def call_api():
    """Example: Call Agno AgentOS API from Python."""
    
    payload = {
        "query": "Hôm nay tôi làm bao nhiêu giờ?",
        "session_id": "session_001"
    }
    
    headers = {
        "Authorization": "Bearer YOUR_TOKEN",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8000/chat",
            json=payload,  # ← httpx handles JSON encoding automatically
            headers=headers
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"Answer: {result['answer']}")
            print(f"Agents: {result['agents_used']}")
            print(f"Metrics: {result['metrics']}")
        else:
            print(f"Error {response.status_code}: {response.text}")

# Run
asyncio.run(call_api())
        '''
        print(code)
    
    def generate_javascript_examples(self):
        """Generate JavaScript/Node.js examples."""
        print_section("JavaScript/Node.js Examples")
        
        code = '''
// Example: Call Agno AgentOS API from JavaScript
const axios = require('axios');

async function callAPI() {
    const payload = {
        query: "Hôm nay tôi làm bao nhiêu giờ?",
        session_id: "session_001"
    };
    
    try {
        const response = await axios.post(
            "http://127.0.0.1:8000/chat",
            payload,  // ← axios handles JSON encoding
            {
                headers: {
                    "Authorization": "Bearer YOUR_TOKEN",
                    "Content-Type": "application/json"
                }
            }
        );
        
        console.log("Answer:", response.data.answer);
        console.log("Agents:", response.data.agents_used);
        console.log("Metrics:", response.data.metrics);
    } catch (error) {
        if (error.response?.status === 400) {
            // JSON format error - client needs to fix
            console.error("JSON Error:", error.response.data);
        } else {
            console.error("API Error:", error.message);
        }
    }
}

callAPI();
        '''
        print(code)


def main():
    """Run all test cases."""
    tester = APITester()
    
    print("\n" + "="*70)
    print("  🚀 Agno AgentOS API — Test Cases & Examples")
    print("="*70)
    
    # Test JSON formatting
    tester.test_json_formatting()
    
    # Generate examples
    tester.generate_curl_examples()
    tester.generate_python_examples()
    tester.generate_javascript_examples()
    
    # Print valid payloads
    print_section("Valid Request Payloads (JSON)")
    requests = tester.get_valid_requests()
    for name, payload in requests.items():
        print(f"\n{name}:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    
    # Key takeaways
    print_section("🎯 Key Points")
    print("""
1. ✅ ALWAYS use double quotes in JSON
   - Correct:   {"query": "test"}
   - Wrong:     {'query': 'test'}

2. ✅ NO trailing commas
   - Correct:   {"query": "test", "session_id": "s1"}
   - Wrong:     {"query": "test", "session_id": "s1",}

3. ✅ Use JSON encoder from your client library
   - Python: httpx automatically encodes json=payload
   - JavaScript: axios automatically encodes
   - curl: Manually create JSON string (NO trailing comma!)

4. ✅ Always include Authorization header
   - Authorization: Bearer <token>

5. ✅ Content-Type header
   - Content-Type: application/json
    """)
    
    print("\n" + "="*70)
    print("  ✅ Examples completed!")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
