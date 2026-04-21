# AgentOS Middleware Authentication for HITC

## Overview

This guide explains how to implement **X-Api-Key** and **Bearer token** authentication for all AgentOS endpoints using the official **middleware pattern**.

**Key Points**:
- ✅ Secures ALL endpoints including `/teams/{id}/runs`
- ✅ Automatically injects `user_id`, `session_id` into every request
- ✅ Works with JWT tokens (Keycloak) and custom X-Api-Key
- ✅ Provides context to MCP tools via session store

---

## Architecture Pattern

```
Client Request
    ↓
HTTP Header (Authorization: Bearer ... OR X-Api-Key: ...)
    ↓
[Middleware] Extract & Validate Token
    ↓
[Middleware] Extract Claims → Build UserPermissionContext
    ↓
[Middleware] Inject: user_id, session_id, dependencies
    ↓
FastAPI App (all routes)
    ↓
AgentOS Routes (/teams/{id}/runs, /teams, etc.)
    ↓
Agent → MCP Tools (access context via session_id)
```

**Execution Order** (from docs):
```
Request: Security Headers → Auth Middleware → Custom Middleware → Route Handler
Response: Route Handler → Custom Middleware → Auth Middleware → Security Headers
```

---

## Solution: Custom Middleware for X-Api-Key + Bearer

### Step 1: Create Authentication Middleware

**File**: `app/middleware/auth_middleware.py`

```python
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging

from app.core.config import get_x_api_key_store, get_keycloak_client
from app.middleware.permission import UserPermissionContext, build_user_context_from_token, build_user_context_from_api_key

logger = logging.getLogger(__name__)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Authenticate requests using X-Api-Key or Bearer token.
    
    - Extracts token from X-Api-Key header or Authorization header
    - Validates token (API key lookup or JWT verification)
    - Builds UserPermissionContext
    - Injects user_id, session_id, dependencies into request state
    - Allows middleware to skip excluded routes
    """
    
    def __init__(self, app, excluded_routes: Optional[list[str]] = None):
        super().__init__(app)
        self.excluded_routes = excluded_routes or [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
        ]
    
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for excluded routes
        if self._is_excluded_route(request.url.path):
            return await call_next(request)
        
        try:
            # Extract authentication credentials
            auth_header = request.headers.get("authorization")
            x_api_key = request.headers.get("x-api-key")
            
            # Build user context
            user = None
            
            if auth_header and auth_header.startswith("Bearer "):
                # JWT Bearer token authentication
                token = auth_header.replace("Bearer ", "")
                user = await build_user_context_from_token(token)
            
            elif x_api_key:
                # X-Api-Key authentication
                user = await build_user_context_from_api_key(x_api_key)
            
            if not user:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required. Use 'Authorization: Bearer <token>' or 'X-Api-Key: <key>' header"},
                )
            
            # Inject user context into request state
            # Available to route handlers via request.state.user
            request.state.user = user
            request.state.user_id = user.user_id
            request.state.session_id = request.query_params.get("session_id", "")
            
            # For AgentOS parameter injection:
            # Extract claims for dependencies parameter
            request.state.dependencies = {
                "user_id": user.user_id,
                "username": user.username,
                "accessible_instances": user.accessible_instance_names,
                "company_code": user.company_code,
                "permissions": user.accessible_instances,
            }
            
            logger.info(f"User authenticated: {user.user_id} ({user.username})")
            
        except HTTPException as e:
            # Re-raise HTTP exceptions (invalid token, etc.)
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": str(e.detail)},
            )
        
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal authentication error"},
            )
        
        # Continue to next middleware/route
        response = await call_next(request)
        return response
    
    def _is_excluded_route(self, path: str) -> bool:
        """Check if route is in excluded list."""
        for excluded in self.excluded_routes:
            if path.startswith(excluded):
                return True
        return False
```

---

### Step 2: Create Helper Functions for JWT + API Key

**File**: `app/middleware/permission.py` (update existing file)

```python
from dataclasses import dataclass
from typing import Optional
import jwt
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


@dataclass
class UserPermissionContext:
    """User context with permissions."""
    user_id: str
    username: str
    accessible_instance_names: list[str]
    accessible_instances: dict[str, list[str]]  # {instance: [permissions]}
    company_code: str


async def build_user_context_from_token(token: str) -> UserPermissionContext:
    """
    Build UserPermissionContext from JWT Bearer token.
    
    Token claims should include:
    - sub: user_id
    - preferred_username: username
    - accessible_instances: {instance_name: [ma_chuc_nang]}
    - company_code: company code
    """
    try:
        # Verify JWT token with Keycloak public key
        keycloak = get_keycloak_client()
        
        # Decode without verification first to get header (for JWKS kid lookup)
        unverified = jwt.decode(
            token,
            options={"verify_signature": False}
        )
        
        # Verify with Keycloak public key
        verified = jwt.decode(
            token,
            key=keycloak.get_public_key(),
            algorithms=["RS256"],
            audience="hitc-client",
        )
        
        # Build context from token claims
        context = UserPermissionContext(
            user_id=verified.get("sub"),
            username=verified.get("preferred_username"),
            accessible_instance_names=list(verified.get("accessible_instances", {}).keys()),
            accessible_instances=verified.get("accessible_instances", {}),
            company_code=verified.get("company_code", ""),
        )
        
        return context
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


async def build_user_context_from_api_key(api_key: str) -> UserPermissionContext:
    """
    Build UserPermissionContext from X-Api-Key.
    
    Looks up API key in database/cache and retrieves associated user context.
    """
    try:
        # Lookup API key in store (cache or database)
        api_key_store = get_x_api_key_store()
        
        user_data = await api_key_store.get(api_key)
        
        if not user_data:
            raise HTTPException(status_code=401, detail="Invalid X-Api-Key")
        
        # Build context from stored user data
        context = UserPermissionContext(
            user_id=user_data.get("user_id"),
            username=user_data.get("username"),
            accessible_instance_names=list(user_data.get("accessible_instances", {}).keys()),
            accessible_instances=user_data.get("accessible_instances", {}),
            company_code=user_data.get("company_code", ""),
        )
        
        return context
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API key lookup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Authentication service error")
```

---

### Step 3: Register Middleware in AgentOS App

**File**: `workflow/hitc_agent.py` (update)

```python
from fastapi import FastAPI
from agno.os import AgentOS
from app.middleware.auth_middleware import AuthenticationMiddleware


def create_hitc_agent_os_app(base_app: Optional[FastAPI] = None) -> FastAPI:
    """
    Create AgentOS with HRM + Document teams and authentication middleware.
    
    Pattern:
    1. Create custom FastAPI app
    2. Add authentication middleware
    3. Pass to AgentOS
    4. Get final app with all routes secured
    """
    
    # Step 1: Create base FastAPI app (if not provided)
    if base_app is None:
        base_app = FastAPI(title="HITC AgentOS")
    
    # Step 2: Add authentication middleware
    # Middleware execution: Auth checks token → Injects user_id into request.state
    base_app.add_middleware(
        AuthenticationMiddleware,
        excluded_routes=[
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
            # Add public routes here
        ]
    )
    
    # Step 3: Create AgentOS with base_app
    agent_os = AgentOS(
        name="HITC AgentOS",
        teams=[hrm_team, doc_team],
        db=db,
        registry=registry,
        base_app=base_app,  # ← Pass custom app with middleware
    )
    
    # Step 4: Get combined app - all routes now have auth middleware
    app = agent_os.get_app()
    
    return app


# Usage in main.py
async def create_app() -> FastAPI:
    return create_hitc_agent_os_app()

if __name__ == "__main__":
    app = create_app()
    # All routes are now secured:
    # - /teams/hrm-team/runs (with auth)
    # - /teams/document-team/runs (with auth)
    # - /hitc/chat (with auth)
    # - /health (no auth - excluded)
```

---

## How It Works

### Request Flow for `/teams/{id}/runs`

```
POST /teams/hrm-team/runs
Header: Authorization: Bearer eyJhbGc...
{
    "message": "What is the salary for Ma01?",
    "session_id": "abc123",
    "user_id": null  # ← Can be empty or any value
}
    ↓
AuthenticationMiddleware.dispatch()
    ↓
[1] Extract Authorization header
[2] Call build_user_context_from_token()
[3] Verify JWT signature with Keycloak public key
[4] Extract claims: user_id, username, accessible_instances, company_code
[5] Build UserPermissionContext
    ↓
request.state.user = UserPermissionContext(...)
request.state.user_id = "user123"  # ← Injected
request.state.session_id = "abc123"
request.state.dependencies = {...}  # ← Claims for MCP tools
    ↓
FastAPI Route Handler
    ↓
AgentOS /teams/{id}/runs endpoint
    ↓
team.arun(
    message="...",
    session_id="abc123",
    user_id="user123",  # ← From middleware
    dependencies={"user_id": "user123", ...}  # ← From middleware
)
    ↓
Workflow Layer
    ↓
Session Store: save_context(session_id="abc123", user_id="user123", ...)
    ↓
Agent Instructions + MCP Tools
    ↓
MCP Tool Execution
    ↓
tools/hrm_tool.py:
    session_context = session_store.get_context(session_id)
    # Validate user_id matches → Check permissions
    # Return filtered data
```

### Request Flow for X-Api-Key

```
POST /teams/{id}/runs
Header: X-Api-Key: sk_prod_abc123def456
{...}
    ↓
AuthenticationMiddleware.dispatch()
    ↓
[1] Extract X-Api-Key header
[2] Call build_user_context_from_api_key()
[3] Lookup API key in store → Get user_data
[4] Build UserPermissionContext from user_data
[5] Same as JWT flow from here...
```

---

## Configuration: JWT Middleware (Alternative)

If you want to use **AgentOS built-in JWT middleware** instead of custom:

```python
from agno.os.middleware import JWTMiddleware
from agno.os.middleware.jwt import TokenSource
from agno.db.postgres import PostgresDb

db = PostgresDb(db_url="postgresql+psycopg://...")

agent_os = AgentOS(
    name="HITC AgentOS",
    teams=[hrm_team, doc_team],
    db=db,
    base_app=FastAPI(),
)

app = agent_os.get_app()

# Add JWT middleware
app.add_middleware(
    JWTMiddleware,
    verification_keys=[get_keycloak_public_key()],
    algorithm="RS256",
    token_source=TokenSource.HEADER,
    user_id_claim="sub",
    session_id_claim="session_id",
    dependencies_claims=["accessible_instances", "company_code"],
    validate=True,
    authorization=False,  # No RBAC for now
    excluded_route_paths=["/health", "/docs"],
)
```

**Pros**: Built-in, automatic claim extraction
**Cons**: Doesn't support X-Api-Key (only JWT), less flexible

---

## Comparison: Custom Middleware vs JWT Middleware

| Feature | Custom Middleware | JWT Middleware |
|---------|-------------------|-----------------|
| **X-Api-Key Support** | ✅ Yes | ❌ No |
| **Bearer Token** | ✅ Yes | ✅ Yes |
| **Automatic Claim Injection** | ⚠️ Manual | ✅ Auto |
| **RBAC Support** | ❌ No | ✅ Yes |
| **Custom Logic** | ✅ Flexible | ❌ Limited |
| **Complexity** | Medium | Low |
| **Best For** | Multiple auth types | JWT-only + RBAC |

---

## Testing

### Test with Bearer Token

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get salary info",
    "session_id": "test123"
  }'
```

### Test with X-Api-Key

```bash
curl -X POST "http://localhost:8000/teams/hrm-team/runs" \
  -H "X-Api-Key: sk_prod_abc123" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Get salary info",
    "session_id": "test123"
  }'
```

### Expected Response (Success)

```json
{
  "message": "...",
  "session_id": "test123",
  "user_id": "user123",
  "data": {...}
}
```

### Expected Response (No Auth)

```json
{
  "detail": "Authentication required. Use 'Authorization: Bearer <token>' or 'X-Api-Key: <key>' header"
}
```

---

## Summary: Authentication Pattern

| Component | Responsibility |
|-----------|-----------------|
| **Custom Middleware** | Extract token from header → Validate → Build UserPermissionContext |
| **Request State** | Store user context (request.state.user) for route handlers |
| **Route Handler** | Access user via request.state.user (if needed) |
| **AgentOS** | Receive user_id, session_id in parameters → Pass to team |
| **Workflow Layer** | Save context in session store |
| **MCP Tools** | Retrieve context from session store → Validate permissions |

---

## Next Steps

1. ✅ Implement `AuthenticationMiddleware` in `app/middleware/auth_middleware.py`
2. ✅ Implement helper functions in `app/middleware/permission.py`
3. ✅ Register middleware in `workflow/hitc_agent.py`
4. ✅ Test with `/teams/{id}/runs` endpoint using X-Api-Key
5. ✅ Verify session context passed to MCP tools
6. ✅ Enable permission validation in MCP tools

---

## References

- [AgentOS Middleware Overview](https://docs.agno.com/agent-os/middleware/overview)
- [AgentOS JWT Middleware](https://docs.agno.com/agent-os/middleware/jwt)
- [AgentOS Custom Middleware](https://docs.agno.com/agent-os/middleware/custom)
- [FastAPI Middleware](https://fastapi.tiangolo.com/tutorial/middleware/)
