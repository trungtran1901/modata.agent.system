# Fix Summary: AgentOS "Function not found" Errors

## Problem

When using AgentOS REST endpoint `/teams/{id}/runs`, users saw repeated errors:
```
ERROR    Function not found
```

But the same functionality worked fine using direct workflow calls:
```
POST /hitc/chat → ✅ Works
POST /teams/{id}/runs → ❌ Function not found
```

---

## Root Cause

**AgentOS Flow (❌ Broken)**:
1. Request arrives: `POST /teams/hrm-team/runs`
2. AgentOS routes to team
3. Calls `team.arun(query)` **WITHOUT** context injection
4. Agents execute with their base instructions only
5. MCP tools called without `session_id` parameter
6. MCP gateway can't find tools
7. ❌ Error: "Function not found"

**Direct Call Flow (✅ Works)**:
1. Request arrives: `POST /hitc/chat`
2. Handler calls `_inject_session_context(session_id, user)`
3. Agent instructions updated to include `session_id`
4. Calls `team.arun(query)` **WITH** context injected
5. Agents execute with session context in instructions
6. MCP tools called with `session_id` parameter
7. ✅ MCP gateway finds tools, executes successfully

**Key Difference**: Context injection happens **before** team execution

---

## Solution

### Part 1: Context Injection Wrapper (New File)

**File**: `workflow/agentosagno_hooks.py`

Creates a wrapper that:
1. Intercepts AgentOS `arun()` and `run()` calls
2. Reconstructs `UserPermissionContext` from `session_id` using `session_store`
3. Calls `_inject_session_context()` to update agent instructions
4. Then calls the original team execution

### Part 2: Apply Wrapper to AgentOS (Modified File)

**File**: `workflow/hitc_agent.py`

Changes:
1. Import wrapper: `from workflow.agentosagno_hooks import get_context_injecting_agent_os`
2. After creating AgentOS: `_agent_os = get_context_injecting_agent_os(_agent_os)`

---

## Files Changed

### New File: `workflow/agentosagno_hooks.py`
```python
"""Context injection wrapper for AgentOS"""

def _reconstruct_user_context_from_session(session_id: str):
    """Retrieve user context from session store using session_id"""
    # Looks up session_id in session_store
    # Returns UserPermissionContext for injection

def get_context_injecting_agent_os(agent_os: AgentOS):
    """Wrap AgentOS to inject context before team execution"""
    # Monkey-patches arun() and run() methods
    # Intercepts calls to inject context
    # Returns same AgentOS instance with context injection enabled
```

### Modified File: `workflow/hitc_agent.py`
```python
# Line 51: Add import
from workflow.agentosagno_hooks import get_context_injecting_agent_os

# Line ~193: In _get_hitc_agent_os() after creating _agent_os
_agent_os = get_context_injecting_agent_os(_agent_os)  # ← Add this line
```

### No Changes Needed: `workflow/session.py`
✅ Already has `get_context()` method for retrieving session data

---

## How It Works (After Fix)

### AgentOS Flow (✅ Now Fixed)
```
1. POST /teams/hrm-team/runs
   └─ session_id: "test-123"

2. AgentOS routes to team
   └─ Calls wrapper.arun(team_id="hrm-team", message="...", session_id="test-123")

3. Wrapper intercepts arun()
   ├─ Retrieves UserPermissionContext from session_store using "test-123"
   ├─ Gets: username="john_doe", company_code="HITC", don_vi_code="HR-001"
   ├─ Calls _inject_session_context("test-123", user_context)
   │  └─ Updates agents' instructions to include session_id and username
   └─ Calls original arun()

4. Team executes with context
   ├─ Agents have session_id in their instructions
   ├─ MCP tools called with session_id parameter
   ├─ MCP gateway resolves tools successfully
   └─ ✅ Response returned

5. Client receives
   ├─ Status: 200 OK
   ├─ Data: Employee information
   └─ No errors
```

---

## Features

### ✅ Automatic Context Reconstruction
- No need to pass UserPermissionContext to AgentOS
- Wrapper automatically retrieves from session_store
- Supports both HRM and Document Intelligence teams

### ✅ Backwards Compatible
- Direct calls (chat_with_hrm_team) still work
- Session context injection still happens before
- Wrapper is an additional safety layer

### ✅ Debug Friendly
- Comprehensive logging with "[ContextInjection]" prefix
- Easy to trace through logs
- Shows when context is injected, retrieved, or skipped

### ✅ Error Handling
- Graceful fallback if session not found
- Skips injection if session_id missing
- Skips if team has no injector
- Detailed warning messages in logs

---

## Testing

### Before Fix
```bash
$ curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: ..." \
  -d '{"message": "Get employee data", "session_id": "test-123"}'

ERROR: Function not found  # ❌ Repeated continuously
```

### After Fix
```bash
$ curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: ..." \
  -d '{"message": "Get employee data", "session_id": "test-123"}'

{
  "message": "Employee data: ...",
  "status": "success"
}  # ✅ Works!
```

---

## Deployment

### Step 1: Add New File
Copy `workflow/agentosagno_hooks.py` to your project

### Step 2: Update Imports
Add to `workflow/hitc_agent.py`:
```python
from workflow.agentosagno_hooks import get_context_injecting_agent_os
```

### Step 3: Apply Wrapper
In `_get_hitc_agent_os()` function, after creating AgentOS:
```python
_agent_os = AgentOS(...)  # Existing code
_agent_os = get_context_injecting_agent_os(_agent_os)  # Add this line
```

### Step 4: Test
```bash
# Test direct call (should still work)
python -m pytest tests/test_chat_with_hitc.py

# Test AgentOS endpoint (should now work)
python -m pytest tests/test_agentosagno_endpoint.py

# Or use curl commands from TESTING_AGENTOSAGNO_FIX.md
```

---

## Logs You'll See

### Context Injection Triggered
```
[ContextInjection] Injecting context: team=hrm-team, session=test-123, user=john_doe
```

### Context Injection Successful
```
[ContextInjection] ✓ Context injected successfully
```

### Skipped (No Session ID)
```
[ContextInjection] Skipped: no session_id
```

### Skipped (Unknown Team)
```
[ContextInjection] Skipped: no injector for team=unknown-team
```

---

## Architecture

```
                    Client Request
                        |
                        v
                    POST /teams/{id}/runs
                    {message: "...", session_id: "test"}
                        |
                        v
                    AgentOS routing
                        |
                        v
                    Wrapper.arun() ← INTERCEPTS HERE
                    {
                        1. Get session_id from params: "test"
                        2. Retrieve UserPermissionContext from session_store
                        3. Call _inject_session_context(session_id, user_context)
                        4. Call original arun()
                    }
                        |
                        v
                    Team execution (with context injected ✓)
                        |
                        v
                    Agent routing to best agent
                        |
                        v
                    MCP Tool call (with session_id in instructions ✓)
                        |
                        v
                    MCP Gateway resolves and executes tool ✓
                        |
                        v
                    Response returned to client
```

---

## Benefits

✅ **Fixes "Function not found" errors**
✅ **No breaking changes** to existing code
✅ **Automatic context management** through wrapper
✅ **Works with all teams** (HRM, Document Intelligence)
✅ **Better logging** for debugging
✅ **Graceful degradation** if session not found
✅ **Production ready**

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Still seeing "Function not found" | Check if wrapper was applied to `_agent_os` in `hitc_agent.py` |
| Context not injecting | Check logs for "[ContextInjection]" messages |
| Session not found | Verify `session_store.get_context()` works and session was saved |
| ImportError in wrapper | Ensure `agentosagno_hooks.py` in correct location: `workflow/` |
| "No context found" | Check MongoDB: `db.hitc_sessions.find()` |

---

## Documentation References

- **Problem Analysis**: `AGENTOSAGNO_DEBUG_FUNCTION_NOT_FOUND.md`
- **Solution Details**: `SOLUTION_AGENTOSAGNO_FUNCTION_NOT_FOUND.md`
- **Testing Guide**: `TESTING_AGENTOSAGNO_FIX.md`
- **Implementation**: This file

---

## Success Metrics

After deploying this fix:

- ✅ `/teams/{id}/runs` endpoint works without errors
- ✅ MCP tools accessible from AgentOS endpoints
- ✅ Session context properly injected in all paths
- ✅ No "Function not found" errors
- ✅ Both direct calls and REST endpoints work identically
- ✅ Logging shows context injection happening

---

## Questions?

Refer to the detailed guides:
1. **Understanding the problem**: `AGENTOSAGNO_DEBUG_FUNCTION_NOT_FOUND.md`
2. **Detailed solution**: `SOLUTION_AGENTOSAGNO_FUNCTION_NOT_FOUND.md`
3. **How to test**: `TESTING_AGENTOSAGNO_FIX.md`
4. **This file**: Quick reference and overview
