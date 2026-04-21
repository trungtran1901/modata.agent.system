# HITC AgentOS - Middleware Authentication Implementation Guide

## Quick Start

### 1. Register Middleware in Main App

**File**: `app/main.py`

```python
from fastapi import FastAPI
from app.middleware.auth_middleware import AuthenticationMiddleware
from workflow.hitc_agent import create_hitc_agent_os_app


async def create_app() -> FastAPI:
    """Create FastAPI app with HITC AgentOS and authentication middleware."""
    
    # Create base FastAPI app
    base_app = FastAPI(
        title="HITC AgentOS",
        description="HRM + Document Intelligence Teams",
        version="1.0.0",
    )
    
    # Add authentication middleware FIRST
    # Middleware execution order: Auth → Custom → Routes
    base_app.add_middleware(
        AuthenticationMiddleware,
        excluded_routes=[
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/metrics",
            "/api/status",  # Add public endpoints here
        ]
    )
    
    # Create AgentOS app with base_app
    # This passes the authenticated request to all AgentOS routes
    hitc_app = create_hitc_agent_os_app(base_app=base_app)
    
    return hitc_app


# Create app instance
app = create_app()

# Add any custom routes BEFORE returning from create_app()
@app.get("/api/status")
async def status():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
```

---

## How It Works

### Request Flow

```
Client Request with Headers
    ↓
FastAPI receive request
    ↓
AuthenticationMiddleware.dispatch()
    ↓
[Decision]
  ├─ Excluded route? (docs, health) → Skip auth → Next middleware
  ├─ X-Api-Key header? → build_user_context_from_api_key()
  └─ Authorization Bearer? → build_user_context_from_token()
    ↓
[Validation]
  ├─ Valid credentials? → Continue
  └─ Invalid? → Return 401 Unauthorized
    ↓
[Injection]
  request.state.user = UserPermissionContext(...)
  request.state.user_id = "user123"
  request.state.session_id = "abc123"
  request.state.dependencies = {...}
    ↓
Route Handler (has access to request.state.user)
    ↓
AgentOS /teams/{id}/runs
    ↓
team.arun(user_id=..., session_id=..., dependencies=...)
    ↓
Workflow → MCP Tools
```

---

## Access User Context in Routes

### In Route Handlers

```python
from fastapi import FastAPI, Request, Depends
from app.middleware.permission import UserPermissionContext

@app.get("/custom/user-info")
async def get_user_info(request: Request):
    """Access authenticated user from request state."""
    
    # Middleware injected this
    user: UserPermissionContext = request.state.user
    user_id: str = request.state.user_id
    session_id: str = request.state.session_id
    
    return {
        "user_id": user_id,
        "username": user.username,
        "accessible_instances": user.accessible_instance_names,
        "company_code": user.company_code,
    }
```

### Using Dependency Injection

```python
from fastapi import Depends, Request
from app.middleware.permission import UserPermissionContext
from typing import Optional


def get_current_user(request: Request) -> Optional[UserPermissionContext]:
    """Dependency to get authenticated user from request state."""
    return getattr(request.state, 'user', None)


@app.post("/custom/agent-run")
async def custom_agent_run(
    request: Request,
    user: UserPermissionContext = Depends(get_current_user),
):
    """Use dependency injection for type safety."""
    
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    return {
        "user_id": user.user_id,
        "message": f"Hello {user.username}",
    }
```

---

## Testing

### Test with Bearer Token

```bash
# Get a JWT token from Keycloak first
TOKEN="eyJhbGciOiJSUzI1NiIsInR5cC..."

# Call /teams/{id}/runs with Bearer token
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for employee Ma01?",
    "session_id": "test-session-123"
  }'
```

### Test with X-Api-Key

```bash
# Call with X-Api-Key header
curl -X POST "http://localhost:8000/teams/document-team/runs" \
  -H "X-Api-Key: sk_prod_abc123def456" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Summarize this document",
    "session_id": "test-session-456"
  }'
```

### Test Excluded Route (No Auth Required)

```bash
# Health check - no authentication needed
curl http://localhost:8000/health

# Docs - no authentication needed
curl http://localhost:8000/docs
```

### Expected Responses

**Success (Authenticated)**:
```json
{
  "message": "...",
  "session_id": "test-session-123",
  "user_id": "user123",
  "data": {...}
}
```

**Error (No Authentication)**:
```json
{
  "detail": "Authentication required",
  "auth_methods": [
    "Authorization: Bearer <jwt_token>",
    "X-Api-Key: <api_key>"
  ]
}
```

**Error (Invalid Token)**:
```json
{
  "detail": "Invalid token: ..."
}
```

---

## JWT Token Format

Expected JWT claims structure:

```json
{
  "sub": "user123",                          // user_id
  "preferred_username": "john.doe",          // username
  "name": "John Doe",                        // full name
  "company_code": "ACME",                    // company
  "accessible_instances": {                  // instances + permissions
    "HR_System": ["SALARY_READ", "SALARY_WRITE"],
    "Document_System": ["VIEW", "DOWNLOAD"],
    "Analytics": ["QUERY"]
  },
  "exp": 1672531200,                         // expiration
  "iat": 1672444800,                         // issued at
  "aud": "hitc-client"                       // audience
}
```

---

## API Key Format & Storage

**API Key Format**:
- Prefix: `sk_prod_` (production) or `sk_test_` (testing)
- Format: `sk_prod_<random_32_chars>`
- Example: `sk_prod_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`

**Storage** (in API key store):
```python
{
    "api_key": "sk_prod_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
    "user_id": "user123",
    "username": "api_user",
    "company_code": "ACME",
    "accessible_instances": {
        "HR_System": ["SALARY_READ"],
        "Document_System": ["VIEW"],
    },
    "created_at": "2024-01-15T10:30:00Z",
    "last_used": "2024-01-20T15:45:00Z",
}
```

---

## Integrating with Session Store

When middleware injects user context, MCP tools can access it:

```python
# In MCP tool (e.g., tools/hrm_tool.py)
from workflow.session import session_store


async def get_salary(employee_id: str, session_id: str) -> dict:
    """MCP tool - access user context from session."""
    
    # Retrieve context saved by workflow
    session_context = session_store.get_context(session_id)
    
    if not session_context:
        return {"error": "No session context"}
    
    user_id = session_context.get("user_id")
    permissions = session_context.get("accessible_context", {})
    
    # Validate permission
    if "SALARY_READ" not in permissions.get("HR_System", []):
        return {"error": "Permission denied"}
    
    # Return salary data
    return {
        "employee_id": employee_id,
        "salary": 50000,
        "user_accessed_by": user_id,
    }
```

---

## Workflow Integration

The authentication middleware works with existing workflow layers:

**hitc_routes.py**:
```python
@hitc_router.post("/chat")
async def hitc_chat(
    req: HitcChatRequest,
    request: Request,  # ← Middleware injected user here
):
    user: UserPermissionContext = request.state.user
    
    result = await chat_with_hitc(
        query=req.query,
        user=user,  # ← Pass to workflow
        session_id=req.session_id,
        history=[],
    )
```

**hitc_agent.py**:
```python
async def chat_with_hitc(query, user, session_id, history):
    # user is now UserPermissionContext from middleware
    
    team_choice = _detect_team(query)
    
    if team_choice == "hrm":
        return await chat_with_hrm_team(query, user, session_id, history)
```

**hrm_team.py**:
```python
async def chat_with_hrm_team(query, user, session_id, history):
    # Save context for MCP tools
    session_store.save_context(
        session_id=session_id,
        user_id=user.user_id,
        username=user.username,
        accessible=user.accessible_instances,
        company_code=user.company_code,
    )
    
    # Rest of handler...
```

---

## Configuration

### Excluded Routes

Routes that skip authentication (modify in `app/main.py`):

```python
base_app.add_middleware(
    AuthenticationMiddleware,
    excluded_routes=[
        "/health",           # Health checks
        "/docs",             # Swagger docs
        "/redoc",            # ReDoc
        "/openapi.json",     # OpenAPI schema
        "/metrics",          # Prometheus metrics
        "/api/status",       # Custom status
        "/static/*",         # Static files (wildcard)
        "/public/*",         # Public endpoints (wildcard)
    ]
)
```

### Logging

Enable debug logging to see authentication flow:

```python
# In config.py
import logging

logging.getLogger("app.middleware.auth_middleware").setLevel(logging.DEBUG)
logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
```

**Log Examples**:
```
INFO  Authenticating with Bearer token
INFO  Built user context from JWT: user123
INFO  User authenticated: user123 (john.doe) via bearer for POST /teams/hrm-team/runs

INFO  Authenticating with X-Api-Key
INFO  User authenticated: api_user (api_user) via api-key for POST /teams/document-team/runs

WARNING Skipping auth for excluded route: /health
WARNING Invalid API key lookup
```

---

## Summary

| Component | Purpose |
|-----------|---------|
| **AuthenticationMiddleware** | Extract token → Validate → Inject user into request.state |
| **build_user_context_from_token()** | Parse JWT Bearer token → Build UserPermissionContext |
| **build_user_context_from_api_key()** | Lookup API key → Build UserPermissionContext |
| **request.state.user** | Access authenticated user in route handlers |
| **request.state.dependencies** | Claims available to AgentOS parameters |
| **Session Store** | Store context for MCP tools to access |

---

## Next Steps

1. ✅ Implement authentication middleware
2. ✅ Register in main FastAPI app  
3. ⏳ Test with Bearer tokens
4. ⏳ Implement API key store lookup
5. ⏳ Test with X-Api-Key
6. ⏳ Enable MCP tool context access
7. ⏳ Document in client SDK

---

## References

- [AuthenticationMiddleware](./app/middleware/auth_middleware.py)
- [Permission Module](./app/middleware/permission.py)
- [AGENTOS_MIDDLEWARE_AUTHENTICATION.md](./AGENTOS_MIDDLEWARE_AUTHENTICATION.md)
- [HITC_AGENTOS_XAPIKEY_GUIDE.md](./HITC_AGENTOS_XAPIKEY_GUIDE.md)
