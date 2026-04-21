# HITC AgentOS: Quick Reference

## One-Minute Overview

**How `chat_with_hitc()` passes user context to MCP tools:**

```
User Request (with Auth)
    ↓
Route Handler (extracts UserPermissionContext)
    ↓
chat_with_hitc(query, user, session_id)
    ↓
Team Handler (saves context, injects into agents)
    ↓
Agent (calls MCP tools with augmented query)
    ↓
MCP Tool (retrieves context, validates, filters results)
    ↓
Permission-filtered Response
```

## Key Points

### 1. Route Layer
**File**: `app/api/routes/hitc_routes.py`

```python
@hitc_router.post("/chat")
async def hitc_chat(req: HitcChatRequest, user: UserPermissionContext = Depends(get_user)):
    result = await chat_with_hitc(query, user, session_id, history)
    return result
```

**Important**: `get_user()` dependency extracts `UserPermissionContext` from auth headers

### 2. Workflow Layer
**File**: `workflow/hitc_agent.py`

```python
async def chat_with_hitc(query, user, session_id, history):
    team_choice = _detect_team(query)
    if team_choice == "document":
        return await chat_with_document_team(query, user, session_id, history)
    return await chat_with_hrm_team(query, user, session_id, history)
```

### 3. Team Handler Layer
**File**: `workflow/hrm_team.py`

```python
async def chat_with_hrm_team(query, user, session_id, history):
    # 1. Save context
    session_store.save_context(session_id, user_id, username, 
                               accessible, company_code)
    
    # 2. Inject into agents
    _inject_session_context(session_id, user)
    
    # 3. Augment query
    aug_query = _augmented_query(session_id, user, query)
    
    # 4. Call team
    response = await team.arun(aug_query, session_id, user_id)
    return response
```

### 4. MCP Tool Layer
**File**: Your MCP tool implementation

```python
def hrm_req_list_requests(session_id: str, month: int, year: int):
    # 1. Get context
    context = session_store.get_context(session_id)
    
    # 2. Validate permission
    if "leave_requests" not in context["accessible_context"]:
        raise PermissionError(f"User {context['username']} denied")
    
    # 3. Query with filter
    results = db.query(..., company_code=context["company_code"])
    
    # 4. Return filtered results
    return results
```

## UserPermissionContext Structure

```python
UserPermissionContext(
    user_id="keycloak-uuid",           # From Keycloak JWT
    username="john.doe",                # User identifier
    email="john.doe@company.com",      # Email
    company_code="ABC",                 # Company
    don_vi_code="HR-01",                # Department
    roles=["HR Manager", "Employee"],   # Roles
    accessible_instance_names=["instance_hrm_01"],  # Accessible data
    permissions={"hrm_req_list_requests": True}     # Tool permissions
)
```

## The Four-Step Context Injection Process

### Step 1: Save Context
```python
session_store.save_context(
    session_id=session_id,
    user_id=user.user_id,
    username=user.username,
    accessible=user.accessible_instance_names,
    company_code=user.company_code,
)
```
**Result**: Context stored in PostgreSQL + Redis

### Step 2: Inject into Agent Instructions
```python
_inject_session_context(session_id, user)
# For each agent:
# agent.instructions = [
#   f'session_id = "{session_id}"',
#   f'username = "{user.username}"',
#   f'company = "{user.company_code}"',
#   ...base_prompt...
# ]
```
**Result**: Agent knows session_id, username, company

### Step 3: Augment Query
```python
aug_query = _augmented_query(session_id, user, query)
# Output: "[session_id:xxx] [username:john] [company:ABC]\n{query}"
```
**Result**: MCP tools can extract context from query prefix

### Step 4: Call Team
```python
response = await team.arun(
    aug_query,
    session_id=session_id,
    user_id=user.user_id
)
```
**Result**: Team/agent execution with full context available

## MCP Tool: Permission Validation Pattern

```python
def tool_name(session_id: str, **kwargs):
    # 1. Retrieve context
    context = session_store.get_context(session_id)
    if not context:
        raise PermissionError("Invalid session")
    
    # 2. Validate permission
    if "required_resource" not in context["accessible_context"]:
        raise PermissionError(f"User denied access")
    
    # 3. Query with filters
    results = db.query(..., 
        company_code=context["company_code"],
        ...)
    
    # 4. Return results
    return results
```

## Session Store Methods

### Save Context
```python
session_store.save_context(
    session_id="uuid-123",
    user_id="keycloak-user-id",
    username="john.doe",
    accessible={"leave_requests": ["instance_1"], "employee_profiles": ["instance_1"]},
    company_code="ABC"
)
```

### Get Context (✅ NEW)
```python
context = session_store.get_context("uuid-123")
# Returns: {
#   "user_id": "keycloak-user-id",
#   "username": "john.doe",
#   "company_code": "ABC",
#   "accessible_context": {...}
# }
```

### Save Messages
```python
session_store.save(session_id, user_id, username, messages)
```

### Load Messages
```python
messages = session_store.load(session_id)
```

## Data Flow Diagram

```
┌─ HTTP ─────────────────────────────┐
│ POST /hitc/chat                     │
│ Authorization: Bearer <token>       │
└────────────────────────────────────┘
           ↓
┌─ Route Handler ────────────────────┐
│ get_user() → UserPermissionContext  │
│ chat_with_hitc(query, user, ...)    │
└────────────────────────────────────┘
           ↓
┌─ Workflow Dispatcher ──────────────┐
│ _detect_team(query)                │
│ chat_with_hrm_team(query, user, ...)│
└────────────────────────────────────┘
           ↓
┌─ Team Handler ─────────────────────┐
│ save_context()                      │
│ _inject_session_context()           │
│ _augmented_query()                  │
│ team.arun()                         │
└────────────────────────────────────┘
           ↓
┌─ Agent Execution ──────────────────┐
│ Route to agent                      │
│ Call MCP tool                       │
└────────────────────────────────────┘
           ↓
┌─ MCP Tool ─────────────────────────┐
│ get_context(session_id)             │
│ validate permission                 │
│ query with filters                  │
│ return results                      │
└────────────────────────────────────┘
           ↓
┌─ Response ─────────────────────────┐
│ Filtered results only for user      │
└────────────────────────────────────┘
```

## Common Tasks

### Task: Query with User Filters in MCP Tool

```python
def my_tool(session_id: str, ...):
    context = session_store.get_context(session_id)
    
    # Apply user's company filter
    results = db.query(
        "SELECT * FROM table WHERE company_code = %s",
        (context["company_code"],)
    )
    
    return results
```

### Task: Check User Permission

```python
def my_tool(session_id: str, ...):
    context = session_store.get_context(session_id)
    
    if "my_resource" not in context["accessible_context"]:
        raise PermissionError(f"User denied")
    
    # Continue with operation
```

### Task: Get User Information in Tool

```python
def my_tool(session_id: str, ...):
    context = session_store.get_context(session_id)
    
    username = context["username"]          # "john.doe"
    company = context["company_code"]       # "ABC"
    don_vi = context.get("don_vi_code")     # "HR-01"
    
    # Use in queries
```

### Task: Test Permission-Aware Tool

```python
# Create test session with permissions
session_store.save_context(
    session_id="test-123",
    user_id="test-user",
    username="testuser",
    accessible={"my_resource": ["instance_1"]},
    company_code="TEST"
)

# Call tool
result = my_tool(session_id="test-123", ...)

# Verify permissions applied
assert result["company"] == "TEST"  # Filtered by company
```

## Files to Review

1. `HITC_AGENTOS_USER_CONTEXT_FLOW.md` - Full architecture
2. `HITC_AGENTOS_INTEGRATION_GUIDE.md` - Integration patterns
3. `MCP_TOOL_TEMPLATE.md` - Tool implementation examples
4. `IMPLEMENTATION_SUMMARY.md` - Complete summary

## Key Implementation Files

| File | Purpose |
|------|---------|
| `app/api/routes/hitc_routes.py` | Route handlers - extract UserPermissionContext |
| `workflow/hitc_agent.py` | Dispatch to appropriate team |
| `workflow/hrm_team.py` | Save context, inject into agents, augment query |
| `workflow/session.py` | Session store with `get_context()` |
| `utils/permission.py` | UserPermissionContext definition |

## Quick Test

```bash
# 1. Send request with auth
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: test-key" \
  -d '{"query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"}'

# 2. Check response
# Should return permission-filtered results

# 3. Verify in code
from workflow.session import session_store
context = session_store.get_context("<session_id_from_response>")
print(f"User: {context['username']}, Company: {context['company_code']}")
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| "Session not found" | Context not saved | Verify `save_context()` called in team handler |
| Permission denied | Tool not in accessible_context | Check accessible_instances when saving context |
| Unfiltered results | No company filter applied | Add `WHERE company_code = context["company_code"]` |
| Wrong user data | Context not retrieved | Call `get_context()` with correct session_id |

## Next: Implement Your MCP Tools

Follow the pattern in `MCP_TOOL_TEMPLATE.md`:

1. Extract session_id from query prefix
2. Call `session_store.get_context(session_id)`
3. Validate user has permission
4. Query database with user filters
5. Return filtered results

See `MCP_TOOL_TEMPLATE.md` for complete examples.

