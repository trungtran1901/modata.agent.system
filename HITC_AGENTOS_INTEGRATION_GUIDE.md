# HITC AgentOS Integration Guide

## How `chat_with_hitc` Works with `create_hitc_agent_os_app`

### Overview

**`create_hitc_agent_os_app()`**: Creates the AgentOS FastAPI application with HRM + Document teams
- **Purpose**: Builds AgentOS with full endpoints
- **Returns**: FastAPI app with auto-exposed `/teams/{id}/runs` endpoints
- **Usage**: Mount into existing FastAPI app in `main.py`

**`chat_with_hitc()`**: Custom chat dispatcher for HITC-specific logic
- **Purpose**: Provides high-level interface for route handlers
- **Handles**: Team detection, user context injection, session management
- **Usage**: Call from `/hitc/chat` route handler to chat with appropriate team

### Architecture

```
main.py
  │
  ├─ Create base FastAPI app
  │
  ├─ Register /hitc/* routes (hitc_routes.py)
  │   └─ POST /hitc/chat
  │       ├─ Extract user context via Depends(get_user)
  │       └─ Call chat_with_hitc()
  │           │
  │           ├─ Detect team (HRM or Document)
  │           ├─ Inject user context into team handlers
  │           └─ Call team.arun() with augmented query
  │
  └─ Mount AgentOS app via create_hitc_agent_os_app(base_app)
      │
      ├─ AgentOS creates teams
      ├─ Registers /teams/* endpoints
      └─ Returns combined FastAPI app
```

### Two Endpoints, Two Purposes

#### 1. Custom `/hitc/chat` Endpoint (Recommended)

**Route**: `app/api/routes/hitc_routes.py`
**Handler**: `hitc_chat()` using `chat_with_hitc()`

**Why use this**:
- ✅ Automatic team detection from query
- ✅ User context properly injected into agents
- ✅ Session management with user permissions
- ✅ Beautiful response format
- ✅ Full permission validation

**How it works**:

```python
# Client request
POST /hitc/chat
Authorization: Bearer <token>
{
  "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
}

# Flow:
hitc_chat() route
  ↓
  get_user() extracts UserPermissionContext
  ↓
  chat_with_hitc(query, user, session_id, history)
  ↓
  _detect_team(query) → "hrm"
  ↓
  chat_with_hrm_team(query, user, session_id, history)
    ├─ session_store.save_context(user) → Save user permissions
    ├─ _inject_session_context(user) → Inject into agent instructions
    ├─ _augmented_query(user, query) → Embed user data in query
    └─ team.arun(aug_query, session_id, user_id) → Call HRM team
  ↓
  HRM team routes to appropriate agent
  ↓
  Agent invokes MCP tools with user context
  ↓
  MCP tools validate permissions and filter results
  ↓
  Response with permission-filtered data

# Client response
{
  "session_id": "uuid-123",
  "answer": "Tháng 4 có 3 đơn đi muộn về sớm",
  "team": "HRM Team",
  "agents": ["hrm_request_agent"]
}
```

**Code Example**:

```python
# In hitc_routes.py
from fastapi import APIRouter, Depends

@hitc_router.post("/chat")
async def hitc_chat(
    req: HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),
) -> HitcChatResponse:
    """
    Call chat_with_hitc() with proper user context.
    chat_with_hitc() handles:
    1. Detecting team (HRM vs Document)
    2. Saving user context
    3. Injecting context into agents
    4. Session management
    """
    sid = req.session_id or str(uuid.uuid4())
    history = session_store.load(sid)
    
    # chat_with_hitc() receives UserPermissionContext and injects it
    result = await chat_with_hitc(
        query=req.query,
        user=user,                    # ← UserPermissionContext
        session_id=sid,
        history=history,
        force_team=req.force_team or "",
    )
    
    return HitcChatResponse(
        session_id=result["session_id"],
        answer=str(result["answer"]),
        team=result.get("team", "HITC AgentOS"),
        agents=result.get("agents", []),
        metrics=result.get("metrics"),
    )
```

#### 2. AgentOS `/teams/{id}/runs` Endpoint (Lower-level)

**Provided by**: `create_hitc_agent_os_app()` from AgentOS
**Endpoints**: 
- `POST /teams/hrm-team/runs`
- `POST /teams/document-team/runs`

**Why NOT to use directly**:
- ❌ Only accepts `user_id: str`, not full `UserPermissionContext`
- ❌ No automatic team detection (must specify team_id)
- ❌ No automatic user context injection
- ❌ No session management built-in
- ❌ Must handle permission validation yourself

**How it works** (if you bypass `chat_with_hitc`):

```python
# Direct client request (NOT RECOMMENDED)
POST /teams/hrm-team/runs
{
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "user_id": "keycloak-uuid",
  "session_id": "uuid-123"
}

# Problem: user_id is just a string
# MCP tools can't validate permissions without full UserPermissionContext

# If you want to use this endpoint directly:
# 1. Extract UserPermissionContext from auth header (use get_user())
# 2. Save to session: session_store.save_context(user)
# 3. Call endpoint with just user_id and session_id
# 4. Endpoint retrieves user context from session
# 5. MCP tools validate permissions via session
```

**Code Example** (if you need direct endpoint):

```python
# NOT RECOMMENDED - but if you need it:
from workflow.hitc_agent import create_hitc_agent_os_app

@router.post("/teams/{team_id}/runs")
async def teams_runs_with_context(
    team_id: str,
    req: dict,
    user: UserPermissionContext = Depends(get_user),
):
    """
    Wrap AgentOS endpoint to inject user context.
    This is what chat_with_hitc() does internally!
    """
    # 1. Save user context in session
    session_store.save_context(
        session_id=req["session_id"],
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )
    
    # 2. Call AgentOS endpoint
    # (This would be handled internally by create_hitc_agent_os_app)
    response = await agentosagno_call(
        f"/teams/{team_id}/runs",
        message=req["message"],
        user_id=user.user_id,
        session_id=req["session_id"],
    )
    
    return response
```

### Integration in `main.py`

**Current Implementation** (Correct):

```python
from fastapi import FastAPI
from workflow.hitc_agent import create_hitc_agent_os_app
from app.api.routes import hitc_routes

# 1. Create base FastAPI app
app = FastAPI(title="HITC API")

# 2. Register custom /hitc/* routes
app.include_router(hitc_routes.hitc_router)
# These routes use chat_with_hitc() ✅

# 3. Mount AgentOS app
app = create_hitc_agent_os_app(base_app=app)
# This adds /teams/* endpoints from AgentOS
# These are lower-level and don't need to be used directly
```

**What Happens**:

1. Custom `/hitc/chat` route (defined in hitc_routes.py) handles user chat requests
   - Extracts `UserPermissionContext` from auth headers
   - Calls `chat_with_hitc()` which injects context properly
   - Returns formatted response

2. AgentOS endpoints `/teams/{id}/runs` are available but lower-level
   - Can be used by other services that manage their own context
   - Not recommended for user-facing API (use `/hitc/chat` instead)

### Data Flow with Both Approaches

```
USER REQUEST
      ↓
   RECOMMENDED: POST /hitc/chat ← Use this
   ├─ Route: hitc_chat(req, user)
   ├─ Function: chat_with_hitc(query, user, session_id, history)
   ├─ Auto: Team detection + context injection
   ├─ Result: Permission-filtered response
   └─ ✅ User context properly handled

   LOWER-LEVEL: POST /teams/hrm-team/runs
   ├─ Route: AgentOS auto-endpoint
   ├─ Input: {message, user_id, session_id}
   ├─ Manual: Must inject context before calling
   ├─ Result: Response (but no auto permission filtering)
   └─ ⚠️ Requires manual context management
```

## Practical Examples

### Example 1: User Asks About Leave Requests

**Request**:
```bash
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: abc123def456" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
  }'
```

**Route Handler** (`hitc_routes.py`):
```python
@hitc_router.post("/chat")
async def hitc_chat(
    req: HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),  # ← Gets user context
):
    sid = req.session_id or str(uuid.uuid4())
    
    result = await chat_with_hitc(
        query="Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
        user=user,  # ← UserPermissionContext with user_id, company_code, permissions
        session_id=sid,
        history=[],
    )
```

**Workflow Dispatcher** (`hitc_agent.py`):
```python
async def chat_with_hitc(
    query="Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    user=UserPermissionContext(...),
    session_id=sid,
    history=[],
):
    team_choice = _detect_team(query)  # → "hrm"
    
    return await chat_with_hrm_team(
        query=query,
        user=user,          # ← Passed down
        session_id=sid,
        history=[],
    )
```

**Team Handler** (`hrm_team.py`):
```python
async def chat_with_hrm_team(
    query="Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    user=UserPermissionContext(
        user_id="keycloak-123",
        username="john.doe",
        company_code="ABC",
        don_vi_code="HR-01",
        permissions={"hrm_req_list_requests": True}
    ),
    session_id=sid,
    history=[],
):
    # Step 1: Save user context
    session_store.save_context(
        session_id=sid,
        user_id="keycloak-123",
        username="john.doe",
        accessible=["instance_hrm_01"],
        company_code="ABC",
    )
    
    # Step 2: Inject into agent instructions
    for agent in _get_agents_cache().values():
        agent.instructions = [
            f'session_id = "{sid}"',
            f'username = "john.doe"',
            f'company = "ABC"',
            f'don_vi = "HR-01"',
            ...base_prompt...
        ]
    
    # Step 3: Augment query with context
    aug_query = "[session_id:sid] [username:john.doe] [don_vi:HR-01] [company:ABC]\nTháng 4 có bao nhiêu đơn đi muộn về sớm?"
    
    # Step 4: Call team
    response = await team.arun(
        aug_query,
        session_id=sid,
        user_id="keycloak-123"
    )
```

**Agent Execution** (AgentOS):
```
1. Agent receives augmented query
2. Agent routes to HRM Request Agent
3. Agent decides to call: hrm_req_list_requests
4. MCP tool receives query with embedded context
```

**MCP Tool** (MCP Gateway):
```python
def hrm_req_list_requests(session_id: str, month: int, year: int) -> list:
    # 1. Retrieve user context from session
    user_context = session_store.get_context(session_id)
    # → UserPermissionContext with company_code="ABC", don_vi_code="HR-01"
    
    # 2. Validate permission
    if not user_context.permissions.get("hrm_req_list_requests"):
        raise PermissionError("User cannot access this")
    
    # 3. Query with filters
    requests = db.query(
        """
        SELECT * FROM leave_requests
        WHERE company_code = %s
        AND don_vi_code = %s
        AND MONTH(request_date) = %s
        AND YEAR(request_date) = %s
        AND type IN ('đi muộn', 'về sớm')
        """,
        (user_context.company_code, user_context.don_vi_code, month, year)
    )
    
    # 4. Return filtered results
    return requests  # → [request1, request2, request3]
```

**Agent Response**:
```
"Tháng 4 có 3 đơn đi muộn về sớm"
```

**API Response**:
```json
{
  "session_id": "uuid-123",
  "answer": "Tháng 4 có 3 đơn đi muộn về sớm",
  "team": "HRM Team",
  "agents": ["hrm_request_agent"],
  "metrics": {
    "total_duration": 2.345,
    "agent_id": "hrm_request_agent"
  }
}
```

### Example 2: Stream Response with SSE

**Request**:
```bash
curl -X POST http://localhost:8000/hitc/chat/stream \
  -H "X-Api-Key: abc123def456" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tóm tắt kết quả chấm công tháng 4"
  }'
```

**Route Handler** (`hitc_routes.py`):
```python
@hitc_router.post("/chat/stream")
async def hitc_chat_stream(
    req: HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),
):
    """Stream response using SSE."""
    sid = req.session_id or str(uuid.uuid4())
    
    async def event_generator():
        async for event in stream_with_hitc(
            query=req.query,
            user=user,              # ← UserPermissionContext
            session_id=sid,
            history=session_store.load(sid),
        ):
            yield event
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
```

**Workflow** (`hitc_agent.py`):
```python
async def stream_with_hitc(
    query: str,
    user: UserPermissionContext,    # ← Received
    session_id: str,
    history: list[dict],
):
    team_choice = _detect_team(query)
    
    if team_choice == "document":
        async for event in stream_with_document_team(
            query=query,
            user=user,              # ← Passed down
            session_id=session_id,
            history=history,
        ):
            yield event
    else:
        async for event in stream_with_hrm_team(
            query=query,
            user=user,              # ← Passed down
            session_id=session_id,
            history=history,
        ):
            yield event
```

**Response Stream**:
```
data: {"content": "Tính toán dữ liệu chấm công..."}
data: {"content": "Tháng 4 có 20 ngày làm việc"}
data: {"content": "Bình quân giờ vào: 8:02, giờ ra: 17:05"}
data: {"metrics": {"total_duration": 3.456}}
```

## Debugging: Verifying User Context Flow

### Enable Debug Logging

In `app/main.py`:
```python
import logging

# Enable debug logs for Agno
logging.getLogger("agno").setLevel(logging.DEBUG)
logging.getLogger("agno.tools.mcp").setLevel(logging.DEBUG)

# Enable debug patches
from utils.debug_tools import apply_debug_patches
apply_debug_patches()
```

### Check Session Store

```python
# In your test code
from workflow.session import session_store

# After chat_with_hitc() is called
context = session_store.get_context(session_id)
print(f"User: {context.username}")
print(f"Company: {context.company_code}")
print(f"Permissions: {context.permissions}")
```

### Verify Agent Instructions

```python
# In your test code
from workflow.hrm_team import _get_agents_cache

agents = _get_agents_cache()
for agent_id, agent in agents.items():
    print(f"Agent {agent_id}:")
    for instruction in agent.instructions[:3]:  # First 3 instructions
        print(f"  - {instruction}")
```

### Check Query Augmentation

```python
# In your test code
from workflow.hrm_team import _augmented_query

aug_query = _augmented_query(
    session_id="test-123",
    user=user_context,
    query="Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
)
print("Augmented Query:")
print(aug_query)
# Output:
# [session_id:test-123] [username:john.doe] [don_vi:HR-01] [company:ABC]
# Tháng 4 có bao nhiêu đơn đi muộn về sớm?
```

## Key Takeaways

1. **Use `/hitc/chat` endpoint** via `chat_with_hitc()` for user-facing API
   - Automatic team detection
   - User context properly injected
   - Session management included
   - ✅ Recommended

2. **AgentOS `/teams/{id}/runs` endpoints** are lower-level
   - Only support `user_id: str`
   - Require manual context injection
   - Available for advanced use cases
   - ⚠️ Don't use for standard user chat

3. **User context flow** is critical for permission validation
   - Extracted at route layer
   - Saved in session at team handler layer
   - Injected into agent instructions
   - Retrieved by MCP tools for permission checks

4. **Always pass `UserPermissionContext`** through the chain
   - Route → Workflow → Team Handler → Agent → MCP Tool
   - Never drop user context along the way

5. **MCP tools validate permissions** using session-stored context
   - Extract user_id from session_id
   - Check user permissions
   - Filter results by user's accessible instances
   - Return only authorized data

