# Testing Guide: AgentOS "Function not found" Fix

## Overview

This guide helps you verify that the fix for "Function not found" errors in AgentOS endpoints is working correctly.

---

## What Was Fixed

**Problem**: 
```
POST /teams/{id}/runs → ERROR: Function not found (repeated)
```

**Root Cause**:
- AgentOS endpoints didn't inject session context into agents
- Agents executed without `session_id` in their instructions
- MCP tools received requests without required `session_id` parameter
- MCP gateway couldn't find tools and returned "Function not found"

**Solution**:
- Created `workflow/agentosagno_hooks.py` with context injection wrapper
- Updated `workflow/hitc_agent.py` to apply wrapper to AgentOS
- Wrapper reconstructs `UserPermissionContext` from `session_store` using `session_id`
- Injects context before `team.arun()` is called

---

## Pre-Test Checklist

- [ ] MCP Gateway is running and accessible
- [ ] MongoDB is running (for session store)
- [ ] FastAPI app is running
- [ ] X-Api-Key is registered in MongoDB
- [ ] Session context can be saved/retrieved

### Verify MCP Gateway
```bash
curl http://localhost:5000/health
# Expected: 200 OK
```

### Verify MongoDB Session Storage
```bash
mongo localhost:27017
db.sessions.find().pretty()
# Should see session data
```

### Verify FastAPI App
```bash
curl http://localhost:8000/health
# Expected: 200 OK
```

---

## Test 1: Direct Call (Baseline - Should Already Work)

### Request
```bash
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Get employee data for John",
    "session_id": "test-session-001"
  }'
```

### Expected Response
```json
{
  "success": true,
  "answer": "...",
  "agent_id": "hrm-employee-agent",
  "time": 2.5
}
```

### Signs of Success
- ✅ No errors
- ✅ MCP tools called successfully
- ✅ Employee data returned
- ✅ Logs show "[ContextInjection] Injecting context..."

### If Failing
```bash
# Check logs
tail -f logs/app.log | grep -i "context\|function\|error"

# Check MCP gateway logs
tail -f logs/mcp_gateway.log | grep -i "session_id\|not found"
```

---

## Test 2: AgentOS Endpoint (Main Fix)

### Request
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data for John",
    "session_id": "test-session-002"
  }'
```

### Expected Response (After Fix)
```json
{
  "message": "...",
  "agent_id": "hrm-employee-agent",
  "status": "success"
}
```

### Signs of Success
- ✅ No "Function not found" errors
- ✅ Response contains employee data
- ✅ Logs show "[ContextInjection] Injecting context..."
- ✅ Logs show "[ContextInjection] ✓ Context injected successfully"

### If Failing (Debugging)
Check for these log patterns:

```bash
# 1. Check if context injection is being triggered
grep "[ContextInjection]" logs/app.log

# Expected:
# [ContextInjection] Injecting context: team=hrm-team, session=test-session-002, user=...
# [ContextInjection] ✓ Context injected successfully
```

```bash
# 2. Check if context is being retrieved from session store
grep "get_context" logs/app.log

# Expected:
# Retrieved session context: user=..., company_code=...
```

```bash
# 3. Check MCP gateway logs for tool calls
grep "hrm_get_employee_info\|session_id" logs/mcp_gateway.log

# Expected:
# Tool called: hrm_get_employee_info with session_id=test-session-002
# ✓ Tool execution successful
```

---

## Test 3: Multiple Sequential Calls

### Purpose
Verify that session context is properly maintained across multiple calls.

### Request 1: Create Session
```bash
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Who am I?",
    "session_id": "test-session-003"
  }'
```

### Request 2: Use Same Session (Direct)
```bash
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Get my leave balance",
    "session_id": "test-session-003"
  }'
```

### Request 3: Use Same Session (AgentOS)
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Show my attendance record",
    "session_id": "test-session-003"
  }'
```

### Expected
- ✅ All three requests succeed
- ✅ Session context maintained across all calls
- ✅ Consistent user identification in responses

---

## Test 4: Document Intelligence Team

### Purpose
Verify context injection works for document team too.

### Request
```bash
curl -X POST "http://localhost:8000/teams/document-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Summarize this contract document",
    "session_id": "test-session-004",
    "document_content": "This is a sample contract...",
    "output_schema": "json"
  }'
```

### Expected Response
```json
{
  "message": "Summary of contract...",
  "agent_id": "document-reader-agent"
}
```

---

## Test 5: Error Cases

### Test 5a: Missing session_id
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data"
  }'
```

### Expected
- Context injection skipped (no session_id)
- Request may still work if no MCP tools need session_id
- Or MCP tools fail with clear "session_id missing" error

### Test 5b: Invalid API Key
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: invalid-key-xyz" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data",
    "session_id": "test-session-005"
  }'
```

### Expected
- 401 Unauthorized
- No context injection occurs

---

## Debugging Logs

### Enable Debug Logging
```python
# In app/core/config.py or app/main.py
import logging

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("workflow").setLevel(logging.DEBUG)
logging.getLogger("agentosagno_hooks").setLevel(logging.DEBUG)
```

### Key Log Patterns to Look For

**1. Context Injection Triggered**
```
[ContextInjection] Injecting context: team=hrm-team, session=test-session-001, user=john_doe
```

**2. Context Retrieved from Session**
```
[ContextInjection] Reconstructed context: user=john_doe
```

**3. Agents Updated with Context**
```
[HRM_TEAM] Agent instructions updated with session context
```

**4. MCP Tool Called Successfully**
```
[MCP_TOOL] hrm_get_employee_info called with session_id=test-session-001
[MCP_TOOL] Tool execution successful, returned 1 result
```

**5. No Error Messages**
```
ERROR    Function not found
```

### If You See These Errors

**Error**: "Function not found"
```bash
# Check:
# 1. Is session_id being passed?
grep "session_id" logs/app.log

# 2. Is MCP gateway accessible?
curl http://localhost:5000/health

# 3. Are agents receiving session context?
grep "[ContextInjection]" logs/app.log
```

**Error**: "No context found for session"
```bash
# Session not stored in session_store
# Check if save_context() was called:
grep "save_context" logs/app.log

# Check MongoDB:
mongo localhost:27017
db.hitc_sessions.find({"_id": "test-session-001"}).pretty()
```

**Error**: "TypeError: unsupported operand type(s)"
```bash
# UserPermissionContext reconstruction might have wrong field types
# Check session store data structure:
mongo localhost:27017
db.hitc_sessions.find().limit(1).pretty()
```

---

## Performance Testing

### Test 6: Load Test

```bash
# Send 10 concurrent requests to AgentOS endpoint
for i in {1..10}; do
  curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
    -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
    -H "Content-Type: application/json" \
    -d "{
      \"message\": \"Get employee data\",
      \"session_id\": \"test-session-$(printf "%03d" $i)\"
    }" &
done
wait
```

### Expected
- ✅ All requests complete successfully
- ✅ No "Function not found" errors
- ✅ Response times are reasonable (<5 seconds each)
- ✅ No memory leaks or connection issues

---

## Success Criteria

✅ **Test 1 Passing**: Direct `/hitc/chat` calls work
✅ **Test 2 Passing**: `/teams/{id}/runs` calls work WITHOUT "Function not found" errors
✅ **Test 3 Passing**: Multiple calls with same session work
✅ **Test 4 Passing**: Document team also works
✅ **Test 5 Passing**: Error cases handled gracefully
✅ **Test 6 Passing**: Load test handles concurrent requests

**Fix is SUCCESSFUL if all tests pass! 🎉**

---

## Rollback (If Needed)

If the fix causes issues, you can disable it:

```python
# In workflow/hitc_agent.py

# Comment out this line:
# _agent_os = get_context_injecting_agent_os(_agent_os)

# This will revert to original behavior (broken, but stable)
```

---

## Next Steps

1. **Run tests**: Execute tests 1-6 above
2. **Monitor logs**: Watch for "[ContextInjection]" messages
3. **Report results**: Share test results and any errors
4. **Fine-tune**: Adjust logging levels or context injection logic as needed
5. **Deploy**: Once all tests pass, deploy to production

---

## Test Script

Here's a Python script to automate testing:

```python
# test_agentosagno_fix.py

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"
API_KEY = "818eccbf414d45918ec7e196de10737d"

HEADERS = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json"
}

def test_direct_call():
    """Test direct /hitc/chat endpoint"""
    print("\n=== Test 1: Direct Call ===")
    
    response = requests.post(
        f"{BASE_URL}/hitc/chat",
        headers=HEADERS,
        json={
            "query": "Get employee data",
            "session_id": "test-direct-001"
        }
    )
    
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    return response.status_code == 200

def test_agentosagno_endpoint():
    """Test /teams/{id}/runs endpoint"""
    print("\n=== Test 2: AgentOS Endpoint ===")
    
    response = requests.post(
        f"{BASE_URL}/teams/hrm-team/runs",
        headers=HEADERS,
        json={
            "message": "Get employee data",
            "session_id": "test-agentosagno-001"
        }
    )
    
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    # Check for "Function not found" error
    response_text = json.dumps(response.json())
    has_function_error = "Function not found" in response_text
    
    print(f"Has 'Function not found' error: {has_function_error}")
    
    return response.status_code == 200 and not has_function_error

def test_document_team():
    """Test document intelligence team"""
    print("\n=== Test 3: Document Team ===")
    
    response = requests.post(
        f"{BASE_URL}/teams/document-team/runs",
        headers=HEADERS,
        json={
            "message": "Summarize this text",
            "session_id": "test-document-001",
            "document_content": "Sample text to summarize"
        }
    )
    
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    return response.status_code == 200

if __name__ == "__main__":
    results = {
        "Direct Call": test_direct_call(),
        "AgentOS Endpoint": test_agentosagno_endpoint(),
        "Document Team": test_document_team(),
    }
    
    print("\n" + "="*50)
    print("TEST RESULTS")
    print("="*50)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name}: {status}")
    
    all_passed = all(results.values())
    print("\n" + ("="*50))
    print(f"Overall: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
```

Run it:
```bash
python test_agentosagno_fix.py
```
