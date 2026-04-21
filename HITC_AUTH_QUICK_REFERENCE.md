# HITC AgentOS Authentication - Quick Reference

## 🚀 Quick Start

### 1. Register Middleware (One-time Setup)

```python
# app/main.py
from fastapi import FastAPI
from app.middleware.auth_middleware import AuthenticationMiddleware
from workflow.hitc_agent import create_hitc_agent_os_app

async def create_app() -> FastAPI:
    base_app = FastAPI(title="HITC AgentOS")
    
    # Add authentication middleware
    base_app.add_middleware(
        AuthenticationMiddleware,
        excluded_routes=["/health", "/docs", "/openapi.json"]
    )
    
    # Create AgentOS (passes middleware through)
    return create_hitc_agent_os_app(base_app=base_app)

app = create_app()
```

### 2. Test with Bearer Token

```bash
TOKEN="eyJhbGc..."  # Get from Keycloak

curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the salary for Ma01?",
    "session_id": "test-123"
  }'
```

### 3. Test with X-Api-Key

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: sk_prod_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get document summary",
    "session_id": "test-456"
  }'
```

---

## 📋 Authentication Methods

| Method | Header | Example |
|--------|--------|---------|
| **Bearer Token** | `Authorization` | `Authorization: Bearer eyJhbGc...` |
| **X-Api-Key** | `X-Api-Key` | `X-Api-Key: sk_prod_abc123...` |
| **Excluded** | None | `/health`, `/docs` |

---

## 🔐 How It Works

```
Client Request
    ↓
[1] AuthenticationMiddleware
    ├─ Extract token from header
    ├─ Validate credentials
    └─ Inject into request.state
    ↓
[2] Route Handler / AgentOS
    ├─ Access: request.state.user
    ├─ Access: request.state.user_id
    └─ user_id automatically passed to AgentOS
    ↓
[3] Team Handler
    └─ Saves context to session_store
    ↓
[4] MCP Tools
    ├─ Retrieve context via session_id
    ├─ Validate permissions
    └─ Return filtered data
```

---

## 👤 Accessing User in Routes

```python
from fastapi import Request
from app.middleware.permission import UserPermissionContext

@app.get("/custom/endpoint")
async def my_endpoint(request: Request):
    # Middleware injected these
    user: UserPermissionContext = request.state.user
    user_id: str = request.state.user_id
    session_id: str = request.state.session_id
    
    return {
        "user_id": user_id,
        "username": user.username,
        "company": user.company_code,
    }
```

---

## 🔑 JWT Token Claims

Expected token structure:

```json
{
  "sub": "user123",
  "preferred_username": "john.doe",
  "accessible_instances": {
    "HR_System": ["SALARY_READ", "SALARY_WRITE"],
    "Document_System": ["VIEW"]
  },
  "company_code": "ACME"
}
```

---

## 🎯 API Key Format

- **Pattern**: `sk_prod_<32_random_chars>` or `sk_test_<32_random_chars>`
- **Example**: `sk_prod_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6`
- **Stored in**: API key store (Redis/Database)

---

## ⚙️ Configuration

### Excluded Routes

```python
excluded_routes=[
    "/health",           # Health checks
    "/docs",             # Swagger docs
    "/redoc",            # ReDoc
    "/openapi.json",     # OpenAPI schema
    "/metrics",          # Prometheus
    "/static/*",         # Static files (wildcard)
]
```

### Enable Debug Logging

```python
import logging

# In config.py or main.py
logging.getLogger("app.middleware.auth_middleware").setLevel(logging.DEBUG)
logging.getLogger("app.middleware.permission").setLevel(logging.DEBUG)
```

---

## 📝 Session Context in MCP Tools

```python
# In MCP tool (e.g., tools/hrm_tool.py)
from workflow.session import session_store

async def get_salary(employee_id: str, session_id: str) -> dict:
    # Get user context from session
    context = session_store.get_context(session_id)
    
    user_id = context.get("user_id")
    permissions = context.get("accessible_context", {})
    
    # Validate permission
    if "SALARY_READ" not in permissions.get("HR_System", []):
        return {"error": "Permission denied"}
    
    # Return data
    return {"employee_id": employee_id, "salary": 50000}
```

---

## 🚨 Error Responses

### No Authentication
```json
{
  "detail": "Authentication required",
  "auth_methods": [
    "Authorization: Bearer <jwt_token>",
    "X-Api-Key: <api_key>"
  ]
}
```
**Status**: 401 Unauthorized

### Invalid Token
```json
{
  "detail": "Invalid token: ..."
}
```
**Status**: 401 Unauthorized

### Expired Token
```json
{
  "detail": "Token expired"
}
```
**Status**: 401 Unauthorized

---

## 🧪 Test Scenarios

### Bearer Token - Success
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer <valid_token>" \
  -d '{"message": "...", "session_id": "..."}'
# Returns: 200 OK + response data
```

### Bearer Token - Expired
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer <expired_token>" \
  -d '{"message": "...", "session_id": "..."}'
# Returns: 401 Unauthorized - Token expired
```

### X-Api-Key - Success
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: sk_prod_<valid_key>" \
  -d '{"message": "...", "session_id": "..."}'
# Returns: 200 OK + response data
```

### X-Api-Key - Invalid
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: sk_prod_invalid" \
  -d '{"message": "...", "session_id": "..."}'
# Returns: 401 Unauthorized - Invalid X-Api-Key
```

### No Authentication
```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -d '{"message": "...", "session_id": "..."}'
# Returns: 401 Unauthorized - Authentication required
```

### Excluded Route (Health)
```bash
curl http://localhost:8000/health
# Returns: 200 OK (no auth required)
```

---

## 🔗 Files Reference

| File | Purpose |
|------|---------|
| `app/middleware/auth_middleware.py` | Middleware implementation |
| `app/middleware/permission.py` | Helper functions & UserPermissionContext |
| `AGENTOS_MIDDLEWARE_AUTHENTICATION.md` | Architecture & patterns |
| `HITC_MIDDLEWARE_IMPLEMENTATION.md` | Full implementation guide |
| `HITC_AGENTOS_AUTHENTICATION_SUMMARY.md` | Overview & summary |
| `SOLUTION_XAPIKEY_FOR_TEAMS_RUNS.md` | Solution explanation |

---

## ✅ Integration Checklist

- [ ] Review `app/middleware/auth_middleware.py`
- [ ] Review `app/middleware/permission.py`
- [ ] Register middleware in `app/main.py`
- [ ] Test with Bearer token
- [ ] Test excluded routes (no auth)
- [ ] Verify user context in routes
- [ ] Verify session context saved
- [ ] Test MCP tool permission validation
- [ ] Enable debug logging
- [ ] Document in API docs
- [ ] Deploy to staging
- [ ] Deploy to production

---

## 📞 Troubleshooting

### "401 Unauthorized - Authentication required"
**Fix**: Add header with token or API key
```bash
# Add this header:
-H "Authorization: Bearer <token>"  # OR
-H "X-Api-Key: <api_key>"
```

### "401 Unauthorized - Invalid token"
**Fix**: Token is malformed or expired
```bash
# Get new token from Keycloak
# Token format: "eyJ..." (not "Bearer eyJ...")
```

### "Authentication error: No module named 'app.utils.perm_store'"
**Fix**: API key store not implemented yet
- Use Bearer token for now
- API key support coming soon

### "401 Unauthorized - Invalid X-Api-Key"
**Fix**: API key store lookup not implemented
- API key support is TODO
- For now, use Bearer token

### User context not in MCP tool
**Fix**: Check session store has context
```python
context = session_store.get_context(session_id)
if context is None:
    # Team handler didn't save context
    # Check hrm_team.py save_context() call
```

---

## 🎓 Key Concepts

### UserPermissionContext
```python
@dataclass
class UserPermissionContext:
    user_id: str                                    # e.g. "user123"
    username: str                                   # e.g. "john.doe"
    accessible_instance_names: list[str]           # e.g. ["HR_System", "Document_System"]
    accessible_instances: dict[str, list[str]]     # e.g. {"HR_System": ["SALARY_READ"]}
    company_code: str                               # e.g. "ACME"
```

### Request State Injection
```python
request.state.user = UserPermissionContext(...)    # Full context
request.state.user_id = "user123"                  # User ID
request.state.session_id = "abc123"                # Session ID
request.state.dependencies = {...}                 # Claims dict
```

### Session Store Integration
```python
# Workflow saves context
session_store.save_context(
    session_id=session_id,
    user_id=user.user_id,
    username=user.username,
    accessible=user.accessible_instances,
    company_code=user.company_code,
)

# MCP tools retrieve context
context = session_store.get_context(session_id)
user_id = context.get("user_id")
```

---

## 🚀 Next Steps

1. **Integrate**: Register middleware in `app/main.py`
2. **Test**: Test all authentication methods
3. **Implement**: API key store lookup (when ready)
4. **Monitor**: Enable debug logging in production
5. **Document**: Update API docs for clients

---

## 📖 Learn More

- [AGENTOS_MIDDLEWARE_AUTHENTICATION.md](./AGENTOS_MIDDLEWARE_AUTHENTICATION.md) - Full guide
- [SOLUTION_XAPIKEY_FOR_TEAMS_RUNS.md](./SOLUTION_XAPIKEY_FOR_TEAMS_RUNS.md) - Problem & solution
- [AgentOS Docs](https://docs.agno.com/agent-os/middleware/overview) - Official docs
