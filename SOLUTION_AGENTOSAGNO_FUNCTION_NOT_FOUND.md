# Solution: Fix AgentOS "Function not found" Errors

## Problem Summary

When calling `/teams/{id}/runs` endpoint (AgentOS REST endpoint):
```
❌ ERROR: Function not found
(repeated continuously)
```

When calling `POST /hitc/chat` (direct workflow call):
```
✅ Works fine, MCP tools execute successfully
```

**Root Cause**: 
- Direct calls: `_inject_session_context(session_id, user)` is called **BEFORE** `team.arun()`
- AgentOS endpoints: AgentOS calls `team.arun()` directly **WITHOUT** context injection
- Result: Agents don't have `session_id` in their instructions
- MCP tools called without `session_id` parameter → "Function not found"

---

## Solution: Two-Part Approach

### Part 1: Enhance AgentOS with Context Injection ✅ DONE

**File**: `workflow/agentosagno_hooks.py` (created)

This file provides:
- `get_context_injecting_agent_os()` - Monkey-patch AgentOS to inject context before execution
- Wraps `arun()` and `run()` methods to call `_inject_session_context()` before team execution

**Implementation**:
```python
# In hitc_agent.py
from workflow.agentosagno_hooks import get_context_injecting_agent_os

def _get_hitc_agent_os() -> AgentOS:
    # ... create _agent_os ...
    _agent_os = get_context_injecting_agent_os(_agent_os)  # ← Wrap with context injection
    return _agent_os
```

### Part 2: Update Agent Initialization

**File**: `workflow/hitc_agent.py` (already updated)

The `_get_hitc_agent_os()` function now wraps the AgentOS instance with context injection.

---

## How It Works

### Before (❌ Broken)
```
1. POST /teams/hrm-team/runs
2. AgentOS receives request
3. AgentOS calls: team.arun(query)
4. Agents execute WITHOUT session context
5. MCP tools called without session_id
6. ❌ MCP gateway: "Function not found"
```

### After (✅ Fixed)
```
1. POST /teams/hrm-team/runs
2. AgentOS receives request
3. Wrapper intercepts: team.arun(query)
4. Wrapper calls: _inject_session_context(session_id, user_context)
5. Agents execute WITH session context injected
6. MCP tools called WITH session_id
7. ✅ MCP gateway finds tools, executes successfully
```

---

## But Wait: We Need the User Context!

**Issue**: Our wrapper can inject context, but AgentOS doesn't pass `UserPermissionContext` to `arun()`.

**Solutions**:

### Option A: Extract from Request Body (Recommended)

AgentOS request format:
```json
POST /teams/hrm-team/runs
{
  "message": "Get employee data",
  "session_id": "test-123"
}
```

We need to:
1. Capture `session_id` from request
2. Look up user context from `session_store` using `session_id`
3. Pass both to context injection

### Option B: Pass Via Custom Headers

Modify requests to include:
```json
POST /teams/hrm-team/runs
{
  "message": "Get employee data",
  "session_id": "test-123",
  "user_id": "emp-456"
}
```

Then look up user from session or directly.

---

## Complete Implementation

### Step 1: Update `agentosagno_hooks.py` to Extract Session

```python
# workflow/agentosagno_hooks.py

async def arun_with_injection(
    team_id: str,
    message: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_context: Optional[UserPermissionContext] = None,
    **kwargs
):
    """
    NOTE: AgentOS might not pass session_id/user_context through arun()
    
    If not, we need to extract from request context or kwargs
    """
    
    # If user_context not provided, try to reconstruct from session_id
    if not user_context and session_id:
        try:
            # Get context from session store
            session_context = session_store.get_context(session_id)
            if session_context:
                # Reconstruct UserPermissionContext
                user_context = UserPermissionContext(
                    user_id=session_context.get('user_id', ''),
                    username=session_context.get('username', 'unknown'),
                    company_code=session_context.get('company_code', ''),
                    don_vi_code=session_context.get('don_vi_code', ''),
                    accessible_instance_names=session_context.get('accessible', []),
                )
                logger.debug(f"[ContextInjection] Reconstructed user context from session: {user_context.username}")
        except Exception as e:
            logger.warning(f"[ContextInjection] Failed to reconstruct context: {e}")
    
    # Now inject if we have both session_id and user_context
    if session_id and user_context and team_id in _CONTEXT_INJECTORS:
        # ... inject context ...
    
    return await original_arun(...)
```

### Step 2: Update `session_store` to Support `get_context()`

**File**: `workflow/session.py`

Ensure `SessionStore` has:
```python
def get_context(self, session_id: str) -> dict:
    """Retrieve session context"""
    # Lookup from storage
    return self.sessions.get(session_id, {})
```

### Step 3: Verify AgentOS Request Format

Check how AgentOS sends requests to understand what's available in `arun()` call.

---

## Testing

### Test 1: Direct Call (should already work)
```bash
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Get employee data",
    "session_id": "test-session-123"
  }'
```

**Expected**: ✅ Works, MCP tools execute

### Test 2: AgentOS Endpoint (currently broken, should be fixed)
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data",
    "session_id": "test-session-123"
  }'
```

**Expected After Fix**: ✅ Works, no "Function not found" errors

---

## Implementation Checklist

- [x] Create `workflow/agentosagno_hooks.py` - Context injection wrapper
- [x] Update `workflow/hitc_agent.py` - Apply wrapper to AgentOS instance
- [x] Create `workflow/agentosagno_middleware.py` - Session context extraction
- [ ] Update `workflow/session.py` - Add `get_context()` method if missing
- [ ] Test both endpoints to verify fix

---

## Next Steps

1. **Verify `session_store` implementation**:
   ```bash
   # Check if get_context() exists
   grep -n "get_context" workflow/session.py
   ```

2. **Add `get_context()` if missing**:
   ```python
   # workflow/session.py
   def get_context(self, session_id: str) -> dict:
       """Get stored session context"""
       return self.sessions.get(session_id, {})
   ```

3. **Test the fix**:
   ```bash
   # Run test_api.py with both endpoints
   python test_api.py
   ```

4. **Debug if still failing**:
   - Check logs for "[ContextInjection]" messages
   - Verify session_id is being passed in request
   - Verify user context is being retrieved from session
   - Check MCP gateway is accessible

---

## Expected Outcome

After implementation:

```
✅ POST /hitc/chat with session_id
   → _inject_session_context() called
   → Agents have session context
   → MCP tools execute
   → ✅ Success

✅ POST /teams/{id}/runs with session_id
   → AgentOS wrapper intercepts
   → _inject_session_context() called
   → Agents have session context
   → MCP tools execute
   → ✅ Success

❌ ERROR "Function not found" - FIXED
```
