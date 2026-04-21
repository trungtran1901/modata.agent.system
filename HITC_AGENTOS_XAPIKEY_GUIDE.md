# Sử dụng HITC AgentOS với X-Api-Key

## Quick Start

### 1. Gửi Request với X-Api-Key

```bash
curl -X POST http://localhost:8000/hitc/chat \
  -H "X-Api-Key: your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tháng 4 có bao nhiêu đơn đi muộn về sớm?",
    "session_id": "optional-session-id"
  }'
```

### 2. Response

```json
{
  "session_id": "uuid-or-provided-id",
  "answer": "Tháng 4 có 3 đơn đi muộn về sớm",
  "team": "HRM Team",
  "agents": ["hrm_request_agent"],
  "metrics": {
    "total_duration": 2.345,
    "agent_id": "hrm_request_agent"
  }
}
```

## Cách Hoạt Động

### Flow: X-Api-Key → User Context → MCP Tools

```
1. Client gửi request với header X-Api-Key
   ↓
2. Route handler (get_user) xác thực:
   context = _perm_svc.build_context_from_api_key(x_api_key)
   ↓
3. Trích xuất UserPermissionContext:
   {
     user_id: "api-key-user-123",
     username: "integration-user",
     company_code: "ABC",
     don_vi_code: "HR-01",
     accessible_instance_names: ["instance_hrm_01"],
     permissions: {"hrm_req_list_requests": true, ...}
   }
   ↓
4. Truyền xuống chat_with_hitc(query, user, session_id, ...)
   ↓
5. Team handler lưu context vào database:
   session_store.save_context(
     session_id="uuid-123",
     user_id="api-key-user-123",
     username="integration-user",
     accessible={"leave_requests": ["instance_hrm_01"]},
     company_code="ABC"
   )
   ↓
6. Inject vào agent instructions + augment query:
   [session_id:uuid-123] [username:integration-user] [company:ABC]
   Tháng 4 có bao nhiêu đơn đi muộn về sớm?
   ↓
7. MCP tools nhận query + có thể lấy context từ session_id
   ↓
8. MCP tools validate permission, filter results
   ↓
9. Response với dữ liệu được phép truy cập
```

## Chi Tiết

### Route Handler (`app/api/routes/hitc_routes.py`)

```python
async def get_user(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> UserPermissionContext:
    """
    Xác thực từ Authorization header hoặc X-Api-Key header.
    """
    # 1. Nếu có Bearer token (Keycloak JWT)
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Invalid Authorization header format")
        try:
            return await _perm_svc.build_context(authorization)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    # 2. Nếu có X-Api-Key
    if x_api_key:
        try:
            return _perm_svc.build_context_from_api_key(x_api_key)
        except PermissionError as e:
            raise HTTPException(401, str(e))

    # 3. Không có auth → 401
    raise HTTPException(
        401,
        "Cần xác thực. Truyền: 'Authorization: Bearer <token>' hoặc 'X-Api-Key: <key>'"
    )

@hitc_router.post("/chat")
async def hitc_chat(
    req: HitcChatRequest,
    user: UserPermissionContext = Depends(get_user),  # ← get_user() gọi tự động
):
    """
    Route handler - get_user() được gọi tự động via Depends().
    Nếu X-Api-Key hợp lệ → user context được trích xuất
    Nếu X-Api-Key không hợp lệ → 401 Unauthorized
    """
    sid = req.session_id or str(uuid.uuid4())
    
    result = await chat_with_hitc(
        query=req.query,
        user=user,  # ← UserPermissionContext từ X-Api-Key
        session_id=sid,
        history=session_store.load(sid),
        force_team=req.force_team or "",
    )
    
    return HitcChatResponse(...)
```

### Permission Service (`utils/permission.py`)

```python
class PermissionService:
    def build_context_from_api_key(self, api_key: str) -> UserPermissionContext:
        """
        Lookup API key trong database và xây dựng UserPermissionContext.
        """
        # 1. Tìm API key trong database
        api_key_record = db.find_api_key(api_key)
        if not api_key_record:
            raise PermissionError(f"Invalid API key")
        
        # 2. Lấy user info
        user = db.get_user(api_key_record["user_id"])
        
        # 3. Xây dựng context
        return UserPermissionContext(
            user_id=user["user_id"],
            username=user["username"],
            email=user["email"],
            company_code=user["company_code"],
            don_vi_code=user["don_vi_code"],
            roles=user.get("roles", []),
            accessible_instance_names=user.get("accessible_instances", []),
            permissions=user.get("permissions", {}),
        )
```

## Ví Dụ Thực Tế

### 1. Tạo API Key cho Integration

**Thường được lưu trong database hoặc .env file:**

```python
# In database: api_keys table
{
  "api_key": "hitc_prod_abc123def456ghi789",
  "user_id": "integration-user-1",
  "name": "Production Integration",
  "created_at": "2024-04-01T10:00:00",
  "expires_at": null,  # null = không hết hạn
  "active": true
}

# Hoặc trong .env
INTEGRATION_API_KEY=hitc_prod_abc123def456ghi789
```

### 2. Gọi API từ Trình Tích Hợp

**Python example:**

```python
import requests

API_URL = "http://localhost:8000/hitc/chat"
API_KEY = "hitc_prod_abc123def456ghi789"

def query_hitc(question: str, session_id: str = None):
    """Gọi HITC AgentOS với X-Api-Key."""
    
    headers = {
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json",
    }
    
    payload = {
        "query": question,
        "session_id": session_id,
    }
    
    response = requests.post(API_URL, json=payload, headers=headers)
    
    if response.status_code == 401:
        print("❌ API Key không hợp lệ")
        return None
    
    if response.status_code == 200:
        result = response.json()
        print(f"✓ Trả lời: {result['answer']}")
        return result
    
    print(f"❌ Lỗi: {response.status_code}")
    return None

# Sử dụng
result = query_hitc("Tháng 4 có bao nhiêu đơn đi muộn về sớm?")
```

**JavaScript/Node.js example:**

```javascript
const API_URL = "http://localhost:8000/hitc/chat";
const API_KEY = "hitc_prod_abc123def456ghi789";

async function queryHitc(question, sessionId = null) {
  const headers = {
    "X-Api-Key": API_KEY,
    "Content-Type": "application/json",
  };

  const payload = {
    query: question,
    session_id: sessionId,
  };

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    if (response.status === 401) {
      console.log("❌ API Key không hợp lệ");
      return null;
    }

    if (response.status === 200) {
      const result = await response.json();
      console.log(`✓ Trả lời: ${result.answer}`);
      return result;
    }

    console.log(`❌ Lỗi: ${response.status}`);
    return null;
  } catch (error) {
    console.error("Error:", error);
    return null;
  }
}

// Sử dụng
queryHitc("Tháng 4 có bao nhiêu đơn đi muộn về sớm?");
```

**cURL example:**

```bash
#!/bin/bash

API_URL="http://localhost:8000/hitc/chat"
API_KEY="hitc_prod_abc123def456ghi789"
QUERY="Tháng 4 có bao nhiêu đơn đi muộn về sớm?"

curl -X POST "$API_URL" \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": \"$QUERY\",
    \"session_id\": \"my-session-123\"
  }"
```

### 3. Streaming Response với X-Api-Key

```bash
curl -X POST http://localhost:8000/hitc/chat/stream \
  -H "X-Api-Key: hitc_prod_abc123def456ghi789" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tóm tắt kết quả chấm công tháng 4"
  }' \
  -N  # Disable buffering for streaming
```

**Response (SSE - Server-Sent Events):**

```
data: {"content": "Đang tính toán..."}
data: {"content": "Tháng 4 có 20 ngày làm việc"}
data: {"content": "Bình quân giờ vào: 8:02, giờ ra: 17:05"}
data: {"metrics": {"total_duration": 3.456}}
```

### 4. Giữ Session Giữa Các Request

```python
import requests

API_URL = "http://localhost:8000/hitc/chat"
API_KEY = "hitc_prod_abc123def456ghi789"
SESSION_ID = "conversation-123"

# Request 1
response1 = requests.post(
    API_URL,
    headers={"X-Api-Key": API_KEY, "Content-Type": "application/json"},
    json={
        "query": "Ai là trưởng phòng HR?",
        "session_id": SESSION_ID,
    }
)
print(f"Q1: {response1.json()['answer']}")

# Request 2 - cùng SESSION_ID
response2 = requests.post(
    API_URL,
    headers={"X-Api-Key": API_KEY, "Content-Type": "application/json"},
    json={
        "query": "Anh ấy quản lý bao nhiêu nhân viên?",
        "session_id": SESSION_ID,  # ← Cùng session
    }
)
print(f"Q2: {response2.json()['answer']}")

# Lịch sử được lưu:
# Q1: "Ai là trưởng phòng HR?"
# A1: "Trưởng phòng HR là Nguyễn Văn A"
# Q2: "Anh ấy quản lý bao nhiêu nhân viên?"
# A2: "Nguyễn Văn A quản lý 15 nhân viên"
```

## Permission & Security

### X-Api-Key Authentication

**Flow:**

```
1. Client → gửi X-Api-Key
           ↓
2. Route handler → xác thực (build_context_from_api_key)
           ↓
3. Nếu hợp lệ → Trích xuất UserPermissionContext
           ↓
4. Nếu không hợp lệ → 401 Unauthorized
```

### User Context từ X-Api-Key

```python
UserPermissionContext(
    user_id="api-key-user-1",              # Từ API key lookup
    username="integration-service",         # Từ database
    email="integration@company.com",        # Từ database
    company_code="ABC",                    # Từ database
    don_vi_code="SYSTEM",                  # Từ database
    roles=["API_CLIENT"],                  # Từ database
    accessible_instance_names=[            # Từ database
        "instance_hrm_01",
        "instance_hrm_02"
    ],
    permissions={                          # Từ database
        "hrm_req_list_requests": True,
        "hrm_emp_view_profile": True,
        "hrm_emp_view_salary": False,  # Không được xem lương
    }
)
```

### Permission Validation trong MCP Tools

**MCP tools nhận augmented query:**

```
[session_id:uuid-123] [username:integration-service] [company:ABC]
Tháng 4 có bao nhiêu đơn đi muộn về sớm?
```

**MCP tool validate:**

```python
def hrm_req_list_requests(session_id: str, month: int, year: int):
    # 1. Get context từ session
    context = session_store.get_context(session_id)
    
    # 2. Check permission
    if not context.permissions.get("hrm_req_list_requests"):
        raise PermissionError(
            f"User {context.username} cannot access hrm_req_list_requests"
        )
    
    # 3. Query với user filters
    requests = db.query(
        "SELECT * FROM leave_requests WHERE company_code = %s AND ...",
        (context.company_code,)
    )
    
    # 4. Return only authorized data
    return requests
```

## Setup API Key

### 1. Database Schema (PostgreSQL)

```sql
CREATE TABLE api_keys (
    id SERIAL PRIMARY KEY,
    api_key TEXT UNIQUE NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    active BOOLEAN DEFAULT TRUE,
    last_used TIMESTAMPTZ,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX idx_api_keys_active ON api_keys(api_key) WHERE active = TRUE;
```

### 2. Generate API Key

```python
import secrets

def generate_api_key(user_id: str, name: str) -> str:
    """Generate secure random API key."""
    # Format: hitc_prod_<random>
    random_part = secrets.token_urlsafe(32)
    api_key = f"hitc_prod_{random_part}"
    
    # Save to database
    db.insert_api_key(
        api_key=api_key,
        user_id=user_id,
        name=name,
        active=True,
    )
    
    return api_key

# Sử dụng
api_key = generate_api_key("integration-user-1", "Production Integration")
print(f"✓ API Key: {api_key}")
```

### 3. Lookup API Key

```python
def build_context_from_api_key(self, api_key: str) -> UserPermissionContext:
    """
    Lookup API key và xây dựng context.
    """
    # 1. Tìm API key
    api_key_record = db.query_api_key(api_key)
    if not api_key_record or not api_key_record["active"]:
        raise PermissionError("Invalid or expired API key")
    
    # 2. Cập nhật last_used
    db.update_api_key(api_key, last_used=datetime.now())
    
    # 3. Lấy user info
    user = db.get_user(api_key_record["user_id"])
    
    # 4. Xây dựng context
    return UserPermissionContext(
        user_id=user["user_id"],
        username=user["username"],
        email=user["email"],
        company_code=user["company_code"],
        don_vi_code=user.get("don_vi_code", "SYSTEM"),
        roles=user.get("roles", ["API_CLIENT"]),
        accessible_instance_names=user.get("accessible_instances", []),
        permissions=user.get("permissions", {}),
    )
```

## Troubleshooting

### "Invalid API key"

```
❌ 401 Unauthorized
{"detail": "Invalid API key"}

→ Kiểm tra:
  1. API key đúng không?
  2. API key active không?
  3. API key chưa hết hạn?
```

### "Permission denied"

```
❌ 403 Forbidden
{"detail": "User does not have permission for this tool"}

→ Kiểm tra:
  1. User liên kết với API key có permission không?
  2. Permission database updated?
```

### Không có response

```
❌ Lỗi timeout / no response

→ Kiểm tra:
  1. MCP Gateway chạy? (http://localhost:8001)
  2. LLM endpoint accessible?
  3. PostgreSQL connected?
```

## Best Practices

1. **Không commit API key** - lưu trong .env hoặc secrets manager
2. **Rotate API key định kỳ** - cập nhật `expires_at`
3. **Log API usage** - để audit trail
4. **Rate limiting** - tránh abuse
5. **Scope permissions** - cấp quyền tối thiểu cần thiết

