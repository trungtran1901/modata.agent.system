# HITC AgentOS User Context Flow

## Overview

This document explains how `UserPermissionContext` flows from the FastAPI route handler through the AgentOS workflow to MCP tools, enabling permission-based access control.

## Architecture: Request → Route → Workflow → Team → Agent → MCP Tool

```
┌─ User Request with Auth Header ─────────────────────────────────────┐
│  POST /hitc/chat                                                     │
│  Authorization: Bearer <token>  OR  X-Api-Key: <api_key>            │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Route Layer (hitc_routes.py) ──────────────────────────────────────┐
│ @hitc_router.post("/chat")                                          │
│ async def hitc_chat(                                                │
│     req: HitcChatRequest,                                           │
│     user: UserPermissionContext = Depends(get_user),  ← EXTRACTED  │
│ ):                                                                  │
│                                                                     │
│   UserPermissionContext contains:                                  │
│   ├── user_id: str                                                 │
│   ├── username: str                                                │
│   ├── email: str                                                   │
│   ├── company_code: str                                            │
│   ├── don_vi_code: str                                             │
│   ├── roles: list[str]                                             │
│   ├── accessible_instance_names: list[str]                        │
│   └── permissions: dict[str, Any]                                  │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Workflow Layer (hitc_agent.py) ────────────────────────────────────┐
│ async def chat_with_hitc(                                           │
│     query: str,                                                     │
│     user: UserPermissionContext,  ← PASSED HERE                    │
│     session_id: str,                                                │
│     ...                                                             │
│ ):                                                                  │
│   ├─ Detect team (HRM or Document)                                 │
│   └─ Dispatch to appropriate handler                               │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Team Handler Layer (hrm_team.py / document_team.py) ────────────────┐
│ async def chat_with_hrm_team(                                        │
│     query: str,                                                     │
│     user: UserPermissionContext,  ← RECEIVED                       │
│     session_id: str,                                                │
│     history: list[dict],                                            │
│ ):                                                                  │
│   │                                                                 │
│   ├─ 1. Save user context in session store:                        │
│   │    session_store.save_context(                                 │
│   │        session_id=session_id,                                  │
│   │        user_id=user.user_id,                                   │
│   │        username=user.username,                                 │
│   │        accessible=user.accessible_instance_names,              │
│   │        company_code=user.company_code,                         │
│   │    )                                                           │
│   │                                                                 │
│   ├─ 2. Inject user context into agent instructions:               │
│   │    _inject_session_context(session_id, user)                   │
│   │    └─ For each agent:                                          │
│   │       agent.instructions = [                                   │
│   │           f'session_id = "{session_id}"',                      │
│   │           f'username = "{user.username}"',                     │
│   │           f'don_vi = "{user.don_vi_code}"',                    │
│   │           f'company = "{user.company_code}"',                  │
│   │           ...agent_base_prompt...                              │
│   │       ]                                                        │
│   │                                                                 │
│   ├─ 3. Augment query with session metadata:                       │
│   │    aug_query = (                                               │
│   │        f"[session_id:{session_id}] "                           │
│   │        f"[username:{user.username}] "                          │
│   │        f"[don_vi:{user.don_vi_code}] "                         │
│   │        f"[company:{user.company_code}]\n{query}"               │
│   │    )                                                           │
│   │                                                                 │
│   └─ 4. Call team with augmented query and session_id:             │
│       response = await team.arun(                                  │
│           aug_query,                                               │
│           session_id=session_id,                                   │
│           user_id=user.user_id                                     │
│       )                                                            │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Agent Execution Layer (AgentOS Team) ──────────────────────────────┐
│ Agent receives:                                                     │
│   ├─ Query with embedded context (username, company, etc.)        │
│   ├─ Instructions with session_id, username, company              │
│   └─ session_id parameter for state retrieval                      │
│                                                                     │
│ When agent needs to call tools:                                    │
│   └─ Agent invokes MCP tools with embedded context                │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ MCP Tool Layer (MCP Gateway @ http://localhost:8001) ───────────────┐
│ Tool receives query with embedded context:                          │
│   "[session_id:xxx] [username:john] [company:ABC] [don_vi:HR-01]  │
│    Tháng 4 có bao nhiêu đơn đi muộn về sớm?"                       │
│                                                                     │
│ Tool parses context from query prefix:                             │
│   ├─ Extracts session_id from "[session_id:xxx]"                  │
│   ├─ Extracts username from "[username:john]"                     │
│   ├─ Extracts company from "[company:ABC]"                        │
│   └─ Uses this context to:                                        │
│       ├─ Validate user permissions                                │
│       ├─ Filter data by accessible_instances                      │
│       ├─ Apply row-level security (RLS)                           │
│       └─ Return only authorized results                           │
│                                                                     │
│ Tool responses are permission-filtered                             │
└────────────────────────────────────────────────────────────────────┘
                              ↓
┌─ Return to User ────────────────────────────────────────────────────┐
│ Response contains only data user has access to                     │
│ {                                                                   │
│   "session_id": "xxx",                                             │
│   "answer": "Tháng 4 có 3 đơn đi muộn về sớm",                   │
│   "team": "HRM Team",                                              │
│   "agents": ["hrm_request_agent"]                                  │
│ }                                                                   │
└────────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Route Layer: `app/api/routes/hitc_routes.py`

**Responsibility**: Extract user context from HTTP headers and pass to workflow.

```python
async def get_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> UserPermissionContext:
    """Extract UserPermissionContext from auth headers."""
    if authorization and authorization.startswith("Bearer "):
        return await _perm_svc.build_context(authorization)  # Keycloak JWT
    
    if x_api_key:
        return _perm_svc.build_context_from_api_key(x_api_key)  # API Key auth
    
    raise HTTPException(401, "Cần xác thực...")

@hitc_router.post("/chat")
async def hitc_chat(
    req: HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),  # ← INJECTED
):
    """Request handler - user context already available."""
    sid = req.session_id or str(uuid.uuid4())
    
    # Pass user context to workflow
    result = await chat_with_hitc(
        query=req.query,
        user=user,          # ← PASSED DOWN
        session_id=sid,
        history=session_store.load(sid),
    )
    
    return HitcChatResponse(...)
```

**Auth Methods**:
- **Bearer Token** (Keycloak JWT):
  ```
  Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
  ```
- **API Key**:
  ```
  X-Api-Key: abc123def456ghi789
  ```

### 2. Workflow Layer: `workflow/hitc_agent.py`

**Responsibility**: Dispatch to appropriate team (HRM or Document).

```python
async def chat_with_hitc(
    query: str,
    user: UserPermissionContext,        # ← RECEIVED
    session_id: str,
    history: list[dict],
    force_team: str = "",
) -> dict:
    """Auto-detect team and dispatch."""
    team_choice = force_team or _detect_team(query)
    
    if team_choice == "document":
        return await chat_with_document_team(
            query=query,
            user=user,                  # ← PASSED DOWN
            session_id=session_id,
            history=history,
        )
    
    # Default: HRM Team
    return await chat_with_hrm_team(
        query=query,
        user=user,                      # ← PASSED DOWN
        session_id=session_id,
        history=history,
    )
```

### 3. Team Handler Layer: `workflow/hrm_team.py` and `workflow/document_team.py`

**Responsibility**: Inject user context into session and agent instructions.

```python
async def chat_with_hrm_team(
    query: str,
    user: UserPermissionContext,        # ← RECEIVED
    session_id: str,
    history: list[dict],
) -> dict:
    """
    Four-step process to inject user context into agents:
    """
    
    # Step 1: Save user context in session store
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )
    
    # Step 2: Inject user context into agent instructions
    _inject_session_context(session_id, user)
    
    # Step 3: Augment query with session metadata
    aug_query = _augmented_query(session_id, user, query)
    # Output: "[session_id:xxx] [username:john] [don_vi:HR-01] [company:ABC]\n{query}"
    
    # Step 4: Call team with augmented query and session_id
    response = await team.arun(
        aug_query,
        session_id=session_id,
        user_id=user.user_id
    )
```

### 4. User Context Data Structure

**From `utils/permission.py`**:

```python
class UserPermissionContext:
    user_id: str                              # Unique user ID (Keycloak sub)
    username: str                             # Username (email-like)
    email: str                                # Email address
    company_code: str                         # Company code (e.g., "ABC")
    don_vi_code: str                          # Department code (e.g., "HR-01")
    roles: list[str]                          # User roles (e.g., ["HR Manager", "Admin"])
    accessible_instance_names: list[str]      # Data instances user can access
    permissions: dict[str, Any]               # Detailed permission map
```

**Example Context**:

```json
{
  "user_id": "keycloak-uuid-12345",
  "username": "john.doe",
  "email": "john.doe@company.com",
  "company_code": "ABC",
  "don_vi_code": "HR-01",
  "roles": ["HR Manager", "Employee"],
  "accessible_instance_names": ["instance_hrm_01", "instance_hrm_02"],
  "permissions": {
    "hrm_req_list_requests": true,
    "hrm_emp_view_profile": true,
    "hrm_emp_view_salary": false
  }
}
```

## How MCP Tools Use User Context

### Parsing Context from Query

When the augmented query reaches an MCP tool, it contains embedded context:

```
Query: "[session_id:uuid-123] [username:john] [don_vi:HR-01] [company:ABC]
Tháng 4 có bao nhiêu đơn đi muộn về sớm?"

Tool parses: 
├─ session_id: "uuid-123"
├─ username: "john"
├─ don_vi: "HR-01"
├─ company: "ABC"
└─ actual_query: "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
```

### Permission Validation Example

```python
# In MCP tool: hrm_req_list_requests
def hrm_req_list_requests(session_id: str, month: int, year: int) -> list:
    # 1. Extract user context from session_id
    user_context = session_store.get_context(session_id)
    
    # 2. Validate user has permission
    if not user_context.permissions.get("hrm_req_list_requests", False):
        raise PermissionError(f"User {user_context.username} cannot list requests")
    
    # 3. Query database with user filters
    requests = db.query_requests(
        company_code=user_context.company_code,
        don_vi_code=user_context.don_vi_code,
        month=month,
        year=year
    )
    
    # 4. Return filtered results
    return requests
```

## Data Flow Summary

| Layer | Component | Input | Processing | Output |
|-------|-----------|-------|-----------|--------|
| **Route** | `hitc_routes.py` | HTTP request + headers | Extract auth → Build UserPermissionContext | UserPermissionContext + query + session_id |
| **Workflow** | `hitc_agent.py` | UserPermissionContext + query | Team detection | Dispatch command |
| **Team Handler** | `hrm_team.py` | UserPermissionContext | Save + Inject + Augment | Augmented query with embedded context |
| **Agent** | AgentOS Team | Augmented query | Route to appropriate agent | Tool invocation |
| **MCP Tool** | Tool Function | Query with embedded context | Parse context → Validate → Filter | Permission-filtered results |

## How to Use in Your Code

### 1. In Route Handler

```python
from fastapi import Depends
from utils.permission import UserPermissionContext

@router.post("/chat")
async def chat(
    query: str,
    user: UserPermissionContext = Depends(get_user),  # ← Get user context
):
    # User context is automatically available
    print(f"User: {user.username}, Company: {user.company_code}")
    
    # Pass to workflow
    result = await chat_with_hitc(
        query=query,
        user=user,  # ← Pass it down
        session_id=session_id,
        history=history,
    )
```

### 2. In Workflow Function

```python
from workflow.hitc_agent import chat_with_hitc
from utils.permission import UserPermissionContext

async def chat_with_hitc(
    query: str,
    user: UserPermissionContext,  # ← Receive from route
    session_id: str,
    history: list[dict],
):
    # Dispatch to appropriate team
    result = await chat_with_hrm_team(
        query=query,
        user=user,  # ← Pass to team handler
        session_id=session_id,
        history=history,
    )
```

### 3. In Team Handler

```python
from workflow.hrm_team import chat_with_hrm_team
from workflow.session import session_store
from utils.permission import UserPermissionContext

async def chat_with_hrm_team(
    query: str,
    user: UserPermissionContext,  # ← Receive from workflow
    session_id: str,
    history: list[dict],
):
    # 1. Save user context in session
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instance_names,
        company_code=user.company_code,
    )
    
    # 2. Inject into agent instructions
    _inject_session_context(session_id, user)
    
    # 3. Augment query with context
    aug_query = _augmented_query(session_id, user, query)
    
    # 4. Call team
    response = await team.arun(
        aug_query,
        session_id=session_id,
        user_id=user.user_id
    )
```

### 4. In MCP Tool (via MCP Gateway)

```python
# In MCP Gateway tool implementation
def hrm_req_list_requests(
    session_id: str,
    month: int,
    year: int
) -> list:
    # Retrieve user context from session
    user_context = session_store.get_context(session_id)
    
    # Validate permission
    if not user_context.permissions.get("hrm_req_list_requests"):
        raise PermissionError(f"User cannot access this tool")
    
    # Query with user filters
    results = db.query_requests(
        company_code=user_context.company_code,
        month=month,
        year=year
    )
    
    return results
```

## Testing the User Context Flow

### Test Query

```bash
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "session_id": "test-session-123"
  }'
```

### Expected Response

```json
{
  "session_id": "test-session-123",
  "answer": "Tháng 4 có 3 đơn đi muộn về sớm (của nhân viên trong đơn vị HR-01)",
  "team": "HRM Team",
  "agents": ["hrm_request_agent"],
  "sources": []
}
```

### Debug Output

With debug logging enabled:

```
INFO HITC dispatch: team=hrm session=test-session-123 user=john.doe
INFO HRM Team handler: saving context for session test-session-123
DEBUG Agent instructions injected with session_id, username, company_code
DEBUG Augmented query: [session_id:test-session-123] [username:john.doe] [don_vi:HR-01] [company:ABC]
       Tháng 4 có bao nhiêu đơn đi muộn về sớm?
DEBUG Calling MCP tool: hrm_req_list_requests with context
DEBUG Tool validated user permissions: OK
DEBUG Tool queried database filtered by: company=ABC, don_vi=HR-01, month=4
DEBUG Tool returned 3 results
INFO Agent processed response and generated answer
```

## Security Considerations

1. **Session Storage**: User context is stored in PostgreSQL/SQLite session store - ensure database is secure
2. **Token Validation**: JWT tokens are validated against Keycloak - verify SSL/TLS for Keycloak connection
3. **API Key Storage**: API keys should be stored securely and rotated regularly
4. **Context in Query**: While context is embedded in query as strings, the actual data operations happen on MCP tools with full validation
5. **RLS Enforcement**: MCP tools MUST implement row-level security (RLS) filters

## Troubleshooting

### Issue: "Function not found" error

**Symptom**: MCP tool invocation fails

**Check**:
1. User context is properly injected into agent instructions
2. Tool name matches available tools in MCP gateway
3. Session ID is valid and context exists in database

**Debug**:
```python
# Enable debug logging in app/main.py
import logging
logging.getLogger("agno").setLevel(logging.DEBUG)
logging.getLogger("agno.tools.mcp").setLevel(logging.DEBUG)
```

### Issue: Streaming doesn't close properly

**Symptom**: SSE stream stays open after response

**Check**:
1. Verify MCP transport type (should be "httpx" not "sse")
2. Check for exceptions in agent execution
3. Ensure proper async context management

### Issue: User context not available in MCP tool

**Symptom**: session_store.get_context() returns None

**Check**:
1. Verify session_id is passed to team.arun()
2. Ensure session_store.save_context() was called before team execution
3. Check session store database is accessible

## Related Files

- `utils/permission.py` - UserPermissionContext definition
- `workflow/session.py` - Session store implementation
- `workflow/hitc_agent.py` - HITC AgentOS factory and dispatch
- `workflow/hrm_team.py` - HRM team handler and context injection
- `workflow/document_team.py` - Document team handler
- `app/api/routes/hitc_routes.py` - FastAPI route handlers
- `app/middleware/permission.py` - Permission middleware

