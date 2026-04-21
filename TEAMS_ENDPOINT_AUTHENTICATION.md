# `/teams/{id}/runs` - Authentication & User Context

## Problem: AgentOS `/teams/{id}/runs` Không Hỗ Trợ Token Authentication

### Issue

`/teams/{id}/runs` endpoint của AgentOS chỉ nhận `user_id` dạng string:

```python
# AgentOS auto-generated endpoint
POST /teams/hrm-team/runs
{
  "message": "query...",
  "user_id": "keycloak-uuid-123",      # ← Chỉ nhận string
  "session_id": "session-456"
}
```

**Bạn KHÔNG thể làm:**

```python
# ❌ KHÔNG ĐƯỢC - AgentOS sẽ treat token như user_id string
POST /teams/hrm-team/runs
{
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "user_id": "Bearer eyJhbGc...",       # ❌ WRONG - token as user_id
  "session_id": "session-456"
}

# ❌ KHÔNG ĐƯỢC - AgentOS sẽ treat API key như user_id string
POST /teams/hrm-team/runs
{
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "user_id": "hitc_prod_abc123...",     # ❌ WRONG - API key as user_id
  "session_id": "session-456"
}
```

### Why Not?

| Vấn đề | Kết quả |
|-------|--------|
| **No Token Validation** | Token không được xác thực, MCP tools không thể validate permissions |
| **No User Context** | Không có company_code, roles, accessible_instances |
| **No Permission Filtering** | Dữ liệu không được filter, user có thể truy cập mọi thứ |
| **Security Risk** | Bất kỳ ai có user_id string cũng có thể impersonate user |

---

## Solution 1: Sử Dụng `/hitc/chat` (Recommended ✅)

### Best Practice

```bash
# ✅ ĐÚNG - Sử dụng /hitc/chat với X-Api-Key hoặc Bearer token
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
  }'
```

**Why this works:**

```
1. Route handler get_user() nhận X-Api-Key
2. Lookup API key → extract user_id, company_code, permissions
3. Build complete UserPermissionContext
4. chat_with_hitc() nhận UserPermissionContext
5. Save vào session + inject vào agents
6. MCP tools get_context() → retrieve full permissions
7. Return filtered results
```

---

## Solution 2: Wrap `/teams/{id}/runs` Endpoint

### Nếu bạn PHẢI dùng AgentOS endpoint

Tạo wrapper endpoint:

```python
# app/api/routes/agentosagno_wrapper.py
from fastapi import APIRouter, Depends, HTTPException
from utils.permission import UserPermissionContext, PermissionService
from workflow.session import session_store
import httpx
import uuid

router = APIRouter(prefix="/api", tags=["AgentOS Wrapper"])
_perm_svc = PermissionService()

async def get_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> UserPermissionContext:
    """Extract UserPermissionContext from token or API key."""
    if authorization and authorization.startswith("Bearer "):
        try:
            return await _perm_svc.build_context(authorization)
        except PermissionError as e:
            raise HTTPException(401, str(e))
    
    if x_api_key:
        try:
            return _perm_svc.build_context_from_api_key(x_api_key)
        except PermissionError as e:
            raise HTTPException(401, str(e))
    
    raise HTTPException(401, "Authentication required")

@router.post("/teams/{team_id}/runs")
async def teams_runs_with_auth(
    team_id: str,
    message: str,
    session_id: Optional[str] = None,
    user: UserPermissionContext = Depends(get_user),  # ← Extract from header
):
    """
    Wrapper for AgentOS /teams/{id}/runs endpoint.
    
    Accepts: X-Api-Key or Bearer token in header
    Returns: Permission-filtered response
    """
    try:
        # 1. Generate session_id if not provided
        sid = session_id or str(uuid.uuid4())
        
        # 2. Save user context to session
        # This allows MCP tools to call session_store.get_context(sid)
        session_store.save_context(
            session_id=sid,
            user_id=user.user_id,
            username=user.username,
            accessible=user.accessible_instance_names,
            company_code=user.company_code,
        )
        
        logger.info(
            "Teams endpoint wrapper: team=%s user=%s session=%s",
            team_id, user.username, sid,
        )
        
        # 3. Call AgentOS endpoint directly
        # AgentOS will route to appropriate team
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://localhost:8000/teams/{team_id}/runs",
                json={
                    "message": message,
                    "user_id": user.user_id,      # ← Pass actual user_id
                    "session_id": sid,             # ← Pass session with context
                },
                timeout=30.0,
            )
        
        if response.status_code != 200:
            logger.error(
                "AgentOS error: %s - %s",
                response.status_code, response.text
            )
            raise HTTPException(response.status_code, response.text)
        
        result = response.json()
        
        # 4. Return response
        return {
            "session_id": sid,
            "message": message,
            "response": result,
            "user": {
                "user_id": user.user_id,
                "username": user.username,
                "company": user.company_code,
            }
        }
        
    except httpx.RequestError as e:
        logger.error("Request error: %s", e)
        raise HTTPException(500, f"AgentOS connection error: {str(e)}")
    except Exception as e:
        logger.error("Wrapper error: %s", e, exc_info=True)
        raise HTTPException(500, str(e))
```

### Usage

```bash
# ✅ Gọi wrapper endpoint với X-Api-Key
curl -X POST http://localhost:8000/api/teams/hrm-team/runs \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "session_id": "optional-session-123"
  }'

# ✅ Hoặc với Bearer token
curl -X POST http://localhost:8000/api/teams/hrm-team/runs \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
  }'
```

### Flow

```
Client Request (with X-Api-Key or Bearer token in header)
    ↓
Wrapper Endpoint (get_user extracts UserPermissionContext)
    ↓
Save context to session (session_store.save_context)
    ↓
Call AgentOS /teams/{id}/runs with user_id + session_id
    ↓
AgentOS processes request
    ↓
MCP tools call session_store.get_context(session_id)
    ↓
MCP tools get full permissions + validate + filter
    ↓
Return permission-filtered response
```

---

## Solution 3: Use AgentOS Built-in Dependencies

### If AgentOS supports custom context injection

```python
# This is theoretical - depends on AgentOS version
POST /teams/hrm-team/runs
{
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "user_id": "keycloak-uuid",
  "session_id": "session-456",
  "dependencies": {
    "user_context": {
      "company_code": "ABC",
      "accessible_instances": ["instance_1"],
      "permissions": {...}
    }
  }
}
```

**Status**: Depends on AgentOS version - check documentation

---

## Comparison: 3 Approaches

| Approach | Auth Header | User Context | Permission Filter | Recommended |
|----------|------------|--------------|-------------------|-------------|
| **1. `/hitc/chat`** | ✅ Supported | ✅ Full | ✅ Auto | ✅ YES |
| **2. Wrapper Endpoint** | ✅ Supported | ✅ Full | ✅ Auto | ⚠️ If needed |
| **3. Direct AgentOS** | ❌ Not supported | ❌ None | ❌ None | ❌ NO |

---

## Implementation Recommendation

### Best Practice Flow

```
PREFERRED:
/hitc/chat
  ↑
  ├─ X-Api-Key in header
  └─ Bearer token in header
     ↓
  Route handler (get_user)
     ↓
  UserPermissionContext
     ↓
  chat_with_hitc() - handles everything
     ↓
  Session save + context injection + permission filtering


FALLBACK (if must use AgentOS endpoint):
/api/teams/{id}/runs (wrapper)
  ↑
  ├─ X-Api-Key in header
  └─ Bearer token in header
     ↓
  Wrapper endpoint (get_user)
     ↓
  UserPermissionContext
     ↓
  Save to session + call AgentOS
     ↓
  AgentOS processes + MCP tools validate
     ↓
  Permission-filtered response


WRONG (don't do this):
/teams/{id}/runs (direct)
  ↑
  └─ Pass token/API key as user_id
     ↓
  ❌ No authentication
  ❌ No permission validation
  ❌ Security risk
```

---

## Why Pass Token/API-Key as user_id Doesn't Work

### Technical Issue

```python
# AgentOS endpoint receives:
POST /teams/hrm-team/runs
{
  "message": "...",
  "user_id": "Bearer eyJhbGc...",     # ← Treated as literal string
  "session_id": "..."
}

# AgentOS treats user_id as simple string:
user_id = "Bearer eyJhbGc..."

# MCP tool tries to use:
context = session_store.get_context(session_id)
if context["user_id"] != user_id:
    # ❌ Fails because user_id is token string, not actual user ID
    raise PermissionError()
```

### No Token Validation

```python
# Token never gets validated
# ❌ Invalid token accepted as valid user_id
# ❌ Expired token accepted
# ❌ Revoked token accepted
# ❌ Malformed token accepted
# ❌ Anyone can claim to be any user

# Example attack:
POST /teams/hrm-team/runs
{
  "message": "Get all employee salaries",
  "user_id": "admin-user-456",       # ← Fake user_id
  "session_id": "fake-session"
}
# ✅ Accepted! No validation!
# ✅ Access admin data with fake user_id!
```

### No Permission Context

```python
# MCP tool needs:
context = {
    "user_id": "keycloak-uuid",
    "username": "john.doe",
    "company_code": "ABC",              # ← Missing!
    "accessible_instances": [...],      # ← Missing!
    "permissions": {...}                # ← Missing!
}

# MCP tool tries:
if context["company_code"] != "ABC":
    # ❌ Fails - company_code not in context
    # Even if you somehow have it, there's no way to verify it

requests = db.query(
    "SELECT * FROM leave_requests WHERE company_code = %s",
    (context["company_code"],)  # ← Empty/None
)
# Returns all data, not filtered!
```

---

## Summary

### ✅ DO Use

1. **`/hitc/chat`** with X-Api-Key or Bearer token in header
   - Recommended, built-in authentication
   - Full user context handling
   - Automatic permission filtering

2. **Wrapper endpoint** for AgentOS if needed
   - Extract token from header
   - Build UserPermissionContext
   - Save to session
   - Call AgentOS with user_id + session_id

### ❌ DON'T Use

1. **Pass token/API-key as user_id** in `/teams/{id}/runs`
   - No token validation
   - No user context
   - No permission filtering
   - Security risk

2. **Direct AgentOS endpoint** without wrapper
   - Skips authentication
   - Skips permission validation
   - Data exposure risk

---

## Code Examples

### ✅ Correct: Using `/hitc/chat`

```bash
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -d '{"query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"}'
```

### ✅ Correct: Using Wrapper Endpoint

```bash
curl -X POST http://localhost:8000/api/teams/hrm-team/runs \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -d '{"message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"}'
```

### ❌ WRONG: Passing Token as user_id

```bash
# ❌ DON'T DO THIS
curl -X POST http://localhost:8000/teams/hrm-team/runs \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "user_id": "Bearer eyJhbGc...",    # ❌ WRONG
    "session_id": "session-123"
  }'

# ❌ DON'T DO THIS
curl -X POST http://localhost:8000/teams/hrm-team/runs \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "user_id": "hitc_prod_abc123...",  # ❌ WRONG
    "session_id": "session-123"
  }'
```

---

## Recommendation

**Use `/hitc/chat` whenever possible.** It's the only endpoint that:
- ✅ Authenticates users (validates X-Api-Key or Bearer token)
- ✅ Builds complete UserPermissionContext
- ✅ Saves context to session
- ✅ Injects context into agents
- ✅ Validates permissions in MCP tools
- ✅ Filters results by company/don_vi

The AgentOS `/teams/{id}/runs` endpoints are lower-level and require you to handle authentication yourself.

