# Comparison: `/hitc/chat` vs `/teams/{id}/runs`

## Quick Comparison

| Feature | `/hitc/chat` | `/teams/{id}/runs` Direct | `/api/teams/{id}/runs` Wrapper |
|---------|------------|----------------------|------------------------------|
| **Authentication** | ✅ Full (X-Api-Key, Bearer) | ❌ None | ✅ Full |
| **User Context** | ✅ Complete | ❌ None | ✅ Complete |
| **Permission Filter** | ✅ Automatic | ❌ None | ✅ Automatic |
| **Team Detection** | ✅ Auto-detect | ❌ Manual (specify team) | ❌ Manual |
| **Session Management** | ✅ Full | ❌ Basic | ✅ Full |
| **Recommended** | ✅ YES | ❌ NO | ⚠️ If needed |
| **Complexity** | ⭐ Simple | ⭐⭐ Medium | ⭐⭐⭐ Complex |

---

## Detailed Comparison

### 1. `/hitc/chat` (Recommended) ✅

#### Request Format

```bash
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "session_id": "optional-session-123"
  }'
```

#### Authentication

```
X-Api-Key or Bearer token in header
    ↓
get_user() dependency
    ↓
build_context_from_api_key() or build_context(token)
    ↓
UserPermissionContext (complete)
```

#### Response

```json
{
  "session_id": "uuid-123",
  "answer": "Tháng 4 có 3 đơn đi muộn về sớm",
  "team": "HRM Team",
  "agents": ["hrm_request_agent"],
  "metrics": {
    "total_duration": 2.345
  }
}
```

#### Advantages

- ✅ Automatic team detection (HRM vs Document)
- ✅ Full permission validation
- ✅ Session management included
- ✅ Beautiful formatted response
- ✅ Streaming support
- ✅ Simple API design

#### Code Flow

```
/hitc/chat
  ↓ get_user()
  ↓ extract UserPermissionContext
  ↓ chat_with_hitc()
  ↓ _detect_team()
  ↓ chat_with_hrm_team() or chat_with_document_team()
  ↓ save_context()
  ↓ _inject_session_context()
  ↓ _augmented_query()
  ↓ team.arun()
  ↓ MCP tools validate + filter
  ↓ response
```

---

### 2. `/teams/{id}/runs` Direct (DON'T USE) ❌

#### Request Format

```bash
curl -X POST http://localhost:8000/teams/hrm-team/runs \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "user_id": "keycloak-uuid-123",
    "session_id": "optional-session"
  }'
```

#### Authentication

**NONE** - No validation!

```
user_id parameter (string, not validated)
    ↓
Treated as literal string
    ↓
No token validation
    ↓
No permission context
```

#### Response

```json
{
  "content": "Tháng 4 có 3 đơn đi muộn về sớm",
  "agent_id": "hrm_request_agent"
}
```

#### Problems

- ❌ No authentication
- ❌ No token validation
- ❌ No user context
- ❌ No permission filtering
- ❌ Must manually specify team
- ❌ Data exposure risk
- ❌ Security vulnerability

#### Code Flow

```
/teams/hrm-team/runs (no auth)
  ↓ user_id parameter (string)
  ↓ AgentOS uses user_id as-is
  ↓ No context building
  ↓ No permission validation
  ↓ MCP tools get no context
  ↓ ❌ All data returned (not filtered)
```

#### Security Issue

```python
# ❌ ATTACK SCENARIO
POST /teams/hrm-team/runs
{
  "message": "Get all employee salary data",
  "user_id": "any-string-value",       # ← No validation!
  "session_id": "fake-session"
}

# ✅ Accepted! Returns all data!
# Attacker didn't need valid credentials
# Just guessed a user_id string
```

---

### 3. `/api/teams/{id}/runs` Wrapper (Fallback) ⚠️

#### Request Format

```bash
curl -X POST http://localhost:8000/api/teams/hrm-team/runs \
  -H "X-Api-Key: hitc_prod_abc123..." \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "session_id": "optional-session-123"
  }'
```

#### Authentication

```
X-Api-Key or Bearer token in header
    ↓
get_user() dependency (in wrapper)
    ↓
build_context_from_api_key()
    ↓
UserPermissionContext (complete)
    ↓
Save to session
    ↓
Call AgentOS with user_id + session_id
```

#### Response

```json
{
  "session_id": "uuid-123",
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "response": {
    "content": "Tháng 4 có 3 đơn đi muộn về sớm",
    "agent_id": "hrm_request_agent"
  },
  "user": {
    "user_id": "keycloak-uuid",
    "username": "john.doe",
    "company": "ABC"
  }
}
```

#### Advantages

- ✅ Full authentication (X-Api-Key, Bearer)
- ✅ User context stored in session
- ✅ Permission validation enabled
- ✅ Can use AgentOS directly
- ✅ Lower-level control

#### Disadvantages

- ⚠️ Manual team specification (must know team_id)
- ⚠️ Need to implement wrapper (extra code)
- ⚠️ More complex setup
- ⚠️ No auto team detection

#### Code Flow

```
/api/teams/{id}/runs (wrapper)
  ↓ get_user()
  ↓ extract UserPermissionContext
  ↓ save_context() to session
  ↓ Call AgentOS /teams/{id}/runs
  ↓ AgentOS processes with user_id + session_id
  ↓ MCP tools validate + filter (using session context)
  ↓ response
```

---

## Decision Tree

```
Do you have authentication header (X-Api-Key or Bearer)?
├─ YES
│  ├─ Do you need auto team detection?
│  │  ├─ YES → Use /hitc/chat ✅
│  │  └─ NO  → Do you need wrapper endpoint?
│  │           ├─ YES → Use /api/teams/{id}/runs ⚠️
│  │           └─ NO  → OK, but you lose features
│  └─ NO
│     └─ Add authentication! (Don't use direct /teams endpoint)
└─ NO
   └─ Add authentication header first!
```

---

## Use Case Examples

### Use Case 1: Web Frontend

**Requirement**: Simple user-facing chat interface

**Solution**: `/hitc/chat` ✅

```python
# Frontend
fetch("/hitc/chat", {
  method: "POST",
  headers: {
    "X-Api-Key": "api-key-from-env",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({query: "..."})
})
```

---

### Use Case 2: Mobile App

**Requirement**: Lightweight API, auto team detection

**Solution**: `/hitc/chat` ✅

```python
# Mobile app
POST /hitc/chat
X-Api-Key: mobile-app-key

{
  "query": "...",
  "session_id": "mobile-session-456"
}
```

---

### Use Case 3: Internal Service Integration

**Requirement**: Service-to-service communication, specific team

**Solution**: `/api/teams/{id}/runs` ⚠️ (if you need specific team)

```python
# Service A → Service B
POST /api/teams/document-team/runs
X-Api-Key: service-to-service-key

{
  "message": "Extract data from document...",
  "session_id": "service-context-789"
}
```

---

### Use Case 4: Direct AgentOS Usage

**Requirement**: Low-level AgentOS control

**Solution**: `/teams/{id}/runs` with wrapper ⚠️

```python
# Advanced use case with custom wrapper
POST /api/teams/hrm-team/runs
Authorization: Bearer jwt-token

{
  "message": "...",
  "session_id": "..."
}
```

---

## Performance Comparison

| Metric | `/hitc/chat` | `/api/teams/...` | Direct `/teams/...` |
|--------|------------|------------------|-------------------|
| **Latency** | ~2.3s | ~2.4s (+0.1s wrapper) | ~2.3s (but unsafe) |
| **Throughput** | Unlimited* | Same* | Same |
| **Memory** | Low | Low | Low |
| **Security** | ✅ Full | ✅ Full | ❌ None |

*Depends on LLM speed

---

## Security Comparison

### `/hitc/chat` (Secure ✅)

```
┌─ Authentication Layer ──────┐
│ X-Api-Key or Bearer token   │
│ Validated against database  │
└─────────────────────────────┘
        ↓
┌─ User Context Layer ────────┐
│ UserPermissionContext built │
│ Company, roles, permissions │
└─────────────────────────────┘
        ↓
┌─ Data Access Layer ─────────┐
│ MCP tools validate + filter │
│ Row-level security applied  │
└─────────────────────────────┘
```

### Direct `/teams/{id}/runs` (Insecure ❌)

```
┌─ NO Authentication Layer ───┐
│ Anyone can call this        │
│ No validation              │
└─────────────────────────────┘
        ↓
┌─ NO User Context Layer ─────┐
│ user_id is just a string    │
│ No roles, no permissions    │
└─────────────────────────────┘
        ↓
┌─ NO Data Access Layer ──────┐
│ MCP tools get no context    │
│ All data returned           │
└─────────────────────────────┘
```

---

## Migration Guide

### If you're currently using Direct `/teams/{id}/runs`

**Problem**: No authentication, no permission filtering

**Step 1: Migrate to `/hitc/chat`**

```python
# Before (WRONG)
POST /teams/hrm-team/runs
{
  "message": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
  "user_id": "user-123",
  "session_id": "session-456"
}

# After (CORRECT)
POST /hitc/chat
X-Api-Key: your-api-key
{
  "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?"
}
```

**Step 2: Update client code**

```python
# Before
response = requests.post(
    "http://localhost:8000/teams/hrm-team/runs",
    json={"message": query, "user_id": user_id, "session_id": sid}
)

# After
response = requests.post(
    "http://localhost:8000/hitc/chat",
    headers={"X-Api-Key": api_key},
    json={"query": query, "session_id": sid}
)
```

**Step 3: Benefit from authentication**

- ✅ API key validated
- ✅ User context loaded
- ✅ Permissions checked
- ✅ Data filtered automatically

---

## Conclusion

| Situation | Endpoint | Reason |
|-----------|----------|--------|
| **Web/Mobile app** | `/hitc/chat` | Simple, secure, auto-detection |
| **Service integration** | `/hitc/chat` | Same benefits, reliable |
| **Need wrapper control** | `/api/teams/{id}/runs` | Custom auth handling |
| **Direct AgentOS** | `/teams/{id}/runs` | ❌ Security risk - avoid! |

**Recommendation**: Always use `/hitc/chat` unless you have a specific reason to use the wrapper.

