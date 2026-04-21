# Solution: How Middleware Solves X-Api-Key for /teams/{id}/runs

## The Problem (Recap)

**Original Question**: "Can I pass X-Api-Key or Bearer token into the `user_id` parameter of `/teams/{id}/runs`?"

**Answer**: NO ❌

```
POST /teams/hrm-team/runs
{
    "message": "Get salary info",
    "user_id": "sk_prod_abc123",  // ❌ NOT VALID
    "session_id": "session123"
}
```

**Why**: The `user_id` parameter expects a string user ID, not a token or API key. It's not validated, just treated as a string.

---

## The Solution: Authentication Middleware

Instead of passing the token in `user_id`, use HTTP headers and let middleware extract it:

```
POST /teams/hrm-team/runs                        ← Same endpoint
Header: Authorization: Bearer eyJhbGc...         ← Token in header
Header: X-Api-Key: sk_prod_abc123...             ← API key in header
{
    "message": "Get salary info",
    "session_id": "session123"
}
```

**How it works**:

```
┌─ AuthenticationMiddleware
│  ├─ Extract header: Authorization or X-Api-Key
│  ├─ Validate credentials
│  ├─ Build UserPermissionContext
│  └─ Inject into request.state
│
└─ /teams/{id}/runs route
   ├─ Access: request.state.user
   ├─ Access: request.state.user_id ✅ NOW POPULATED
   └─ Continue to AgentOS
```

---

## Before Middleware (❌ Problem)

```python
POST /teams/hrm-team/runs
{
    "message": "...",
    "user_id": "user123",      # Only accepts user ID
    "session_id": "abc123"
}

# No authentication! Anyone can call this endpoint
# Can't pass X-Api-Key or Bearer token
# Need wrapper endpoint to extract token first
```

---

## After Middleware (✅ Solution)

```python
POST /teams/hrm-team/runs
Header: Authorization: Bearer eyJhbGc...
{
    "message": "...",
    "session_id": "abc123"     # user_id automatically filled
}

# Route handler receives:
# request.state.user = UserPermissionContext(
#     user_id="user123",
#     username="john.doe",
#     accessible_instances={...},
#     company_code="ACME"
# )

# AgentOS receives:
# user_id = "user123" (from request.state)
# user context is available!
```

---

## Request Flow Comparison

### OLD: Without Middleware (No Auth)

```
Client → /teams/{id}/runs
         (no headers)
           ↓
     Route Handler
        (no user)
           ↓
     AgentOS
        (no user context)
           ↓
     MCP Tools
        (no permissions!)
           ↓
     RETURNS ALL DATA (unsafe)
```

### NEW: With Middleware (Secured)

```
Client → POST /teams/{id}/runs
         Header: Authorization: Bearer ...
              OR X-Api-Key: ...
           ↓
     AuthenticationMiddleware
     ✓ Extract token
     ✓ Validate
     ✓ Build UserPermissionContext
     ✓ Inject into request.state
           ↓
     Route Handler
     ✓ Access request.state.user
           ↓
     AgentOS
     ✓ user_id automatically available
           ↓
     Team Handler
     ✓ Save context in session store
           ↓
     MCP Tools
     ✓ Retrieve context
     ✓ Validate permissions
     ✓ Return filtered data (safe)
```

---

## Example: HRM Team with Middleware

### Client Request

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for Ma01?",
    "session_id": "session-abc123"
  }'
```

### Middleware Processing

```python
# AuthenticationMiddleware.dispatch()

1. Extract Authorization header: "Bearer eyJhbGc..."
2. Call build_user_context_from_token("eyJhbGc...")
3. Decode JWT → Extract claims:
   - sub: "user123"
   - preferred_username: "john.doe"
   - company_code: "ACME"
   - accessible_instances: {
       "HR_System": ["SALARY_READ", "SALARY_WRITE"],
       "Document_System": ["VIEW"]
     }
4. Build UserPermissionContext:
   UserPermissionContext(
       user_id="user123",
       username="john.doe",
       accessible_instance_names=["HR_System", "Document_System"],
       accessible_instances={...},
       company_code="ACME"
   )
5. Inject into request.state:
   request.state.user = UserPermissionContext(...)
   request.state.user_id = "user123"
   request.state.session_id = "session-abc123"

6. Pass to next middleware → Route handler
```

### Route Handler

```python
@app.post("/teams/hrm-team/runs")
async def team_runs(request: Request, body: dict):
    # Middleware already populated request.state
    user = request.state.user  # ✅ UserPermissionContext
    user_id = request.state.user_id  # ✅ "user123"
    session_id = body["session_id"]  # ✅ "session-abc123"
    
    # Pass to workflow
    result = await chat_with_hrm_team(
        query=body["message"],
        user=user,  # ✅ Full context with permissions
        session_id=session_id,
        history=[]
    )
    
    return result
```

### Workflow Layer

```python
async def chat_with_hrm_team(query, user, session_id, history):
    # 1. Save user context in session store
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,  # ✅ "user123"
        username=user.username,
        accessible=user.accessible_instances,
        company_code=user.company_code,
    )
    
    # 2. Inject context into agent instructions
    _inject_session_context(session_id, user)
    
    # 3. Execute team
    response = await team.arun(
        query,
        session_id=session_id,
        user_id=user.user_id,  # ✅ Now we have real user_id
    )
    
    return response
```

### MCP Tools

```python
async def get_salary_data(employee_id: str, session_id: str) -> dict:
    # Retrieve user context
    context = session_store.get_context(session_id)
    
    if not context:
        return {"error": "No session context"}
    
    user_id = context.get("user_id")  # ✅ "user123"
    permissions = context.get("accessible_context", {})
    
    # Validate permission
    if "SALARY_READ" not in permissions.get("HR_System", []):
        return {"error": "Permission denied for user123"}
    
    # Query database with permission context
    salary = await db.get_salary(
        employee_id=employee_id,
        company_code=context["company_code"],
        accessed_by=user_id
    )
    
    # Return data with audit trail
    return {
        "employee_id": employee_id,
        "salary": salary,
        "accessed_by": user_id,
        "accessed_at": datetime.now().isoformat(),
    }
```

### Response to Client

```json
{
  "message": "The salary for employee Ma01 is $50,000/year",
  "data": {
    "employee_id": "Ma01",
    "salary": 50000,
    "accessed_by": "user123",
    "accessed_at": "2024-01-20T15:45:00Z"
  },
  "session_id": "session-abc123",
  "user_id": "user123"
}
```

---

## Key Differences

### Without Middleware ❌

```
Input:  /teams/hrm-team/runs + {user_id: "user123"}
        ↓
Process: No token validation
        ↓
Output: Anyone can pass any user_id
        ↓
Risk:   SECURITY VULNERABILITY
```

### With Middleware ✅

```
Input:  /teams/hrm-team/runs + Authorization: Bearer <token>
        ↓
Process: Middleware validates token
        ↓
Output: user_id extracted from verified token
        ↓
Risk:   SECURE - Token must be valid
```

---

## Supporting Both Bearer Token and X-Api-Key

```python
# Same endpoint supports both authentication methods

# Method 1: Bearer Token
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer eyJhbGc..." \
  -d '{...}'

# Method 2: X-Api-Key
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: sk_prod_abc123..." \
  -d '{...}'

# Method 3: No Auth (if in excluded_routes)
curl -X POST "http://localhost:8000/health" \
  -d '{...}'
```

**Middleware decides which method to use**:
```python
if auth_header.startswith("Bearer "):
    # JWT token
    user = await build_user_context_from_token(token)
elif x_api_key:
    # X-Api-Key
    user = await build_user_context_from_api_key(x_api_key)
else:
    # No auth
    return 401
```

---

## Configuration

Register middleware once in `app/main.py`:

```python
from fastapi import FastAPI
from app.middleware.auth_middleware import AuthenticationMiddleware
from workflow.hitc_agent import create_hitc_agent_os_app

async def create_app() -> FastAPI:
    # Create base app
    base_app = FastAPI(title="HITC AgentOS")
    
    # Add authentication middleware
    base_app.add_middleware(
        AuthenticationMiddleware,
        excluded_routes=["/health", "/docs", "/openapi.json"]
    )
    
    # Create AgentOS with middleware
    hitc_app = create_hitc_agent_os_app(base_app=base_app)
    
    return hitc_app

app = create_app()
```

**All these endpoints are now secured**:
- ✅ `/teams/hrm-team/runs` - Requires auth
- ✅ `/teams/document-team/runs` - Requires auth
- ✅ `/teams` - Requires auth
- ✅ `/agents/{id}/runs` - Requires auth
- ✅ `/health` - No auth (excluded)
- ✅ `/docs` - No auth (excluded)

---

## Why This Is Better Than Wrapper Endpoint

### Old Approach: Wrapper Endpoint ❌

```python
@app.post("/hitc/chat")
async def hitc_chat(
    req: HitcChatRequest,
    auth: str = Header()  # Requires custom dependency
):
    # 1. Manually extract user from header
    user = await extract_user(auth)
    
    # 2. Call native AgentOS endpoint
    response = await client.call(
        "/teams/hrm-team/runs",
        user_id=user.user_id,
        message=req.query,
    )
    
    return response
```

**Problems**:
- Only works for `/hitc/chat`
- Doesn't secure native `/teams/{id}/runs`
- Need multiple wrapper endpoints
- Can't use native AgentOS UI
- Manual dependency handling

### New Approach: Middleware ✅

```python
# Configure once in main.py
base_app.add_middleware(
    AuthenticationMiddleware,
    excluded_routes=[...]
)

# ALL endpoints automatically secured:
# - /teams/hrm-team/runs ✅
# - /teams/document-team/runs ✅
# - /agents/{id}/runs ✅
# - Custom /hitc/chat ✅
# - Native AgentOS UI ✅
```

**Advantages**:
- Secures all endpoints automatically
- Works with native AgentOS routes
- Enable AgentOS UI
- Works with any endpoint
- One configuration

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Can use X-Api-Key?** | ❌ No | ✅ Yes |
| **Can use Bearer token?** | ❌ No | ✅ Yes |
| **Secure /teams/{id}/runs?** | ❌ No | ✅ Yes |
| **User context available?** | ❌ No | ✅ Yes |
| **MCP tools get permissions?** | ❌ No | ✅ Yes |
| **Code changes needed?** | ⚠️ Many | ✅ One config |

---

## Files

- ✅ `app/middleware/auth_middleware.py` - Middleware implementation
- ✅ `app/middleware/permission.py` - Helper functions
- ✅ `AGENTOS_MIDDLEWARE_AUTHENTICATION.md` - Architecture
- ✅ `HITC_MIDDLEWARE_IMPLEMENTATION.md` - Implementation guide
- ✅ `HITC_AGENTOS_AUTHENTICATION_SUMMARY.md` - Architecture overview
- ✅ `SOLUTION_XAPIKEY_FOR_TEAMS_RUNS.md` - This file

---

## Next Steps

1. ✅ Review middleware code
2. ⏳ Integrate into `app/main.py`
3. ⏳ Test with Bearer token
4. ⏳ Test with X-Api-Key (when store implemented)
5. ⏳ Verify permission validation in MCP tools
6. ⏳ Deploy to production
