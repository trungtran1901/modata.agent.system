# Debug: AgentOS "Function not found" Error

## Problem

When calling `/teams/{id}/runs` endpoint (AgentOS native), you see:
```
ERROR Function not found
```

But when calling `POST /hitc/chat` (which uses `chat_with_hitc()`), MCP tools work fine.

---

## Root Cause Analysis

The difference is in how the two paths handle context:

### Path 1: `chat_with_hitc()` ✅ WORKS
```
1. Route receives user context from middleware
2. Call: chat_with_hitc(query, user, session_id, ...)
3. Inside: _inject_session_context(session_id, user)
   └─ Updates agent.instructions with session_id
4. Call: team.arun(query, session_id=session_id, user_id=...)
5. Agent has session_id in context
6. MCP tools called with session_id parameter
7. ✅ Tools work
```

### Path 2: `/teams/{id}/runs` ❌ FAILS
```
1. AgentOS endpoint receives request
2. Call: team.arun() WITHOUT context injection
3. Agents don't have session_id in instructions
4. MCP tools called WITHOUT session_id parameter
5. ❌ MCP gateway can't find session_id
6. ❌ Error: "Function not found"
```

---

## Solution: Ensure Context Injection for AgentOS

The issue is that AgentOS endpoints (`/teams/{id}/runs`) don't automatically call `_inject_session_context()`.

We need to ensure that when requests come through AgentOS, they still get the session context injected.

### Option 1: Add Middleware to Inject Context (Recommended)

Create a middleware that injects session context for all team requests:

**File**: `workflow/context_injection_middleware.py`

```python
"""
Middleware to inject session context into agents before AgentOS processing.

This ensures that both direct team calls and AgentOS endpoints have access to
session_id and user context for MCP tools.
"""
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from workflow.session import session_store
from workflow.hrm_team import _inject_session_context as _hrm_inject

logger = logging.getLogger(__name__)


class ContextInjectionMiddleware(BaseHTTPMiddleware):
    """
    Inject session context into agents before request is processed.
    
    This ensures MCP tools have access to session_id and user info.
    """
    
    async def dispatch(self, request: Request, call_next):
        # Only process /teams/* endpoints
        if not request.url.path.startswith("/teams/"):
            return await call_next(request)
        
        try:
            # Extract session_id and user from request.state (set by auth middleware)
            session_id = getattr(request.state, 'session_id', '')
            user = getattr(request.state, 'user', None)
            
            if session_id and user:
                logger.debug(f"Injecting context: session={session_id}, user={user.username}")
                
                # Inject context into agents
                # This ensures MCP tools have access to session_id
                _hrm_inject(session_id, user)
        
        except Exception as e:
            logger.warning(f"Error injecting context: {e}")
        
        return await call_next(request)
```

### Option 2: Wrap Team.arun() to Inject Context

Update the team creation to include context injection before running:

**File**: `workflow/hitc_agent.py`

```python
# Add before creating agents
from workflow.hrm_team import _inject_session_context as _hrm_inject
from workflow.document_team import _inject_session_context as _doc_inject


async def _team_arun_wrapper(team, query, session_id, user, **kwargs):
    """Wrapper to inject context before team.arun()"""
    # Inject session context
    if team.id == "hrm-team":
        _hrm_inject(session_id, user)
    elif team.id == "document-team":
        _doc_inject(session_id, user)
    
    # Call team.arun() with context injected
    return await team.arun(query, session_id=session_id, user_id=user.user_id, **kwargs)
```

---

## Immediate Workaround

### Option A: Use Custom Wrapper Routes (Quickest)

Create wrapper routes that ensure context injection:

```python
# app/api/routes/team_wrapper.py

from fastapi import APIRouter, Request, HTTPException
from workflow.hitc_agent import chat_with_hitc, stream_with_hitc
from utils.permission import UserPermissionContext

router = APIRouter(prefix="/team-wrapper", tags=["Team Wrapper"])


@router.post("/hrm/runs")
async def hrm_team_runs(request: Request, body: dict):
    """Wrapper for HRM team that ensures context injection"""
    
    user: UserPermissionContext = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Use chat_with_hitc which properly injects context
    result = await chat_with_hitc(
        query=body.get("message", ""),
        user=user,
        session_id=body.get("session_id", ""),
        history=[],
        force_team="hrm"
    )
    
    return result


@router.post("/document/runs")
async def document_team_runs(request: Request, body: dict):
    """Wrapper for Document team that ensures context injection"""
    
    user: UserPermissionContext = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    result = await chat_with_hitc(
        query=body.get("message", ""),
        user=user,
        session_id=body.get("session_id", ""),
        history=[],
        force_team="document",
        document_content=body.get("document_content", ""),
        output_schema=body.get("output_schema", "")
    )
    
    return result
```

Register in main.py:
```python
from app.api.routes.team_wrapper import router as team_wrapper_router
app.include_router(team_wrapper_router)
```

---

## Recommended Solution

### Step 1: Create Context Injection Middleware

```python
# workflow/context_injection_middleware.py
# (code above)
```

### Step 2: Register Middleware in main.py

```python
from workflow.context_injection_middleware import ContextInjectionMiddleware
from app.middleware.auth_middleware import AuthenticationMiddleware

async def create_app() -> FastAPI:
    base_app = FastAPI()
    
    # Add auth middleware FIRST
    base_app.add_middleware(
        AuthenticationMiddleware,
        excluded_routes=[...]
    )
    
    # Add context injection middleware SECOND
    base_app.add_middleware(
        ContextInjectionMiddleware
    )
    
    # Create AgentOS with base_app
    return create_hitc_agent_os_app(base_app=base_app)
```

### Step 3: Verify with Test

```bash
# Test /teams/{id}/runs with session_id
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: 818eccbf414d45918ec7e196de10737d" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get employee data",
    "session_id": "test-session-123"
  }'
```

**Expected**: ✅ MCP tools called with session_id, no "Function not found" error

---

## Why This Happens

**AgentOS Flow**:
1. Request comes to `/teams/{id}/runs`
2. AgentOS routes it to the team
3. Team calls agents directly
4. Agents execute with their **current instructions** (no session context)
5. MCP tools called but lack session_id
6. MCP gateway tries to find tool without session context
7. ❌ "Function not found"

**Context Injection Fixes This**:
1. Request comes to `/teams/{id}/runs`
2. **Middleware injects session context**
3. Agents' instructions updated with session_id
4. Team calls agents
5. Agents execute with **injected session context**
6. MCP tools called with session_id
7. MCP gateway finds tool successfully
8. ✅ Works!

---

## Debugging Steps

If still seeing "Function not found":

### 1. Enable Debug Logging

```python
import logging
logging.getLogger("workflow").setLevel(logging.DEBUG)
```

### 2. Check Agent Instructions

```python
# In Python shell
from workflow.hrm_team import _get_agents_cache

agents = _get_agents_cache()
for aid, agent in agents.items():
    print(f"{aid}:")
    print(f"  Instructions: {agent.instructions[:100]}...")
    print(f"  Tools: {[t.__class__.__name__ for t in agent.tools]}")
```

### 3. Check MCP Gateway Logs

```bash
# Check MCP gateway is running
curl http://localhost:5000/health  # or your MCP_GATEWAY_URL
```

### 4. Check Session ID in MCP Tool Call

Add debug logging to MCP tool wrapper:

```python
# In MCP tool (e.g., tools/hrm_tool.py)
import logging

logger = logging.getLogger(__name__)

async def get_employee_data(employee_id: str, session_id: str) -> dict:
    logger.info(f"MCP Tool called: session_id={session_id}, employee_id={employee_id}")
    
    if not session_id:
        logger.error("ERROR: session_id is empty!")
        return {"error": "No session context"}
    
    # Rest of tool...
```

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `workflow/context_injection_middleware.py` | CREATE | Inject context for AgentOS requests |
| `app/main.py` | UPDATE | Register middleware |
| `workflow/hitc_agent.py` | VERIFY | Ensure context injection happens |

---

## Expected Outcome

After implementing context injection:

```
✅ POST /hitc/chat with X-Api-Key
✅ POST /teams/{id}/runs with X-Api-Key
✅ Both paths call MCP tools successfully
✅ No "Function not found" errors
✅ Session context available in all paths
```

---

## Testing

```bash
# Test both paths
echo "=== Testing /hitc/chat ==="
curl -X POST "http://localhost:8000/hitc/chat" \
  -H "X-Api-Key: ..." \
  -d '{"query": "...", "session_id": "test-123"}'

echo ""
echo "=== Testing /teams/{id}/runs ==="
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: ..." \
  -d '{"message": "...", "session_id": "test-123"}'

# Both should work without "Function not found" errors
```
