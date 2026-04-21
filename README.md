# MODATA.AGENT — AI Agent API

FastAPI + Agno Agent kết nối MCP Gateway (`modata-mcp`) để xử lý hội thoại
với dữ liệu nội bộ doanh nghiệp.

Project này **không** bao gồm MCP tools hay embedding pipeline.
Cần chạy kết hợp với `modata-mcp` (MCP Gateway).

## Kiến trúc

```
Client
  │  Bearer JWT (Keycloak)
  ▼
modata-agent (port 8000)
  │  verify JWT → build UserPermissionContext
  │  lưu accessible_instance_names → PostgreSQL
  │
  ├── Agno Agent (LLM: Qwen3-8B remote)
  │     │  SSE tool calls
  │     ▼
  │   modata-mcp (port 8001)          ← project riêng
  │     ├── data_*    (MongoDB)
  │     ├── analytics_* (MongoDB)
  │     ├── docs_*    (Qdrant RAG)
  │     ├── tools_*   (utils)
  │     └── mail_*    (SMTP)
  │
  └── PostgreSQL (session history + permission context)
```

## Cấu trúc

```
modata-agent/
├── app/
│   ├── api/routes/routes.py    # /chat, /chat/session/* endpoints
│   ├── core/config.py          # Config đọc từ .env
│   ├── db/mongo.py             # MongoDB client (permission lookup)
│   └── main.py                 # FastAPI entry point
├── utils/
│   └── permission.py           # Keycloak JWT verify + MongoDB RBAC
├── workflow/
│   ├── agent.py                # Agno Agent core logic
│   └── session.py              # PostgreSQL session store
├── Dockerfile
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
└── run.py
```

## API

### Chat

```http
POST /chat
Authorization: Bearer <keycloak_jwt>
Content-Type: application/json

{
  "query": "Nhân viên Nguyễn Văn A đã làm việc bao lâu?",
  "session_id": "optional-uuid"
}
```

**Response:**
```json
{
  "session_id": "uuid",
  "answer": "Nhân viên Nguyễn Văn A đã làm việc 6 năm 2 tháng 10 ngày...",
  "sources": []
}
```

### Session

```http
GET  /chat/session/{session_id}   # Lấy lịch sử hội thoại
DELETE /chat/session/{session_id} # Xoá lịch sử
```

### Health

```http
GET /health  →  {"status": "ok"}
```

## Khởi động nhanh

### Yêu cầu

- `modata-mcp` đang chạy tại `http://localhost:8001/sse` (hoặc cấu hình `MCP_GATEWAY_URL` trong `.env`)

### 1. Chuẩn bị .env

```bash
cp .env.example .env
# Chỉnh sửa .env — quan trọng nhất: MCP_GATEWAY_URL, KEYCLOAK_*, MONGO_URI
```

### 2. Docker (khuyến nghị)

```bash
# Production
docker compose up -d --build

# Development (hot reload)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Logs
docker compose logs -f api

# Dừng
docker compose down
```

### 3. Local

```bash
pip install -r requirements.txt
python run.py
# API tại http://localhost:8000
```

## Cơ chế hoạt động

### Phân quyền

```
JWT → Keycloak verify → MongoDB (nhan_vien, phan_quyen, sys_conf_view)
    → UserPermissionContext (accessible_instance_names)
    → lưu vào PostgreSQL.rag_sessions
    → modata-mcp đọc theo session_id để kiểm tra quyền từng tool call
```

### Session

Mỗi request có `session_id` (tự sinh nếu không truyền).
Lịch sử hội thoại lưu tại `PostgreSQL.rag_sessions.messages`.
`accessible_instance_names` cũng lưu trong bảng này để MCP Gateway đọc.

## 📚 Documentation

### User Context Flow & Permission Integration

Để hiểu cách `UserPermissionContext` được truyền từ route → workflow → team → agent → MCP tool:

- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** ⚡ - Bắt đầu từ đây! (1-5 phút)
  - Sơ đồ flow, pattern cơ bản, ví dụ nhanh

- **[HITC_AGENTOS_USER_CONTEXT_FLOW.md](HITC_AGENTOS_USER_CONTEXT_FLOW.md)** - Chi tiết kiến trúc (15 phút)
  - Complete architecture diagram
  - Từng layer chi tiết (route → workflow → team → agent → MCP tool)
  - Cách MCP tool sử dụng user context
  - Security considerations

- **[HITC_AGENTOS_INTEGRATION_GUIDE.md](HITC_AGENTOS_INTEGRATION_GUIDE.md)** - Cách tích hợp (20 phút)
  - Cách dùng `chat_with_hitc()` vs `create_hitc_agent_os_app()`
  - Hai integration patterns: recommended vs lower-level
  - Practical examples (leave requests, streaming)
  - Debugging techniques

- **[HITC_AGENTOS_XAPIKEY_GUIDE.md](HITC_AGENTOS_XAPIKEY_GUIDE.md)** - X-Api-Key authentication (15 phút)
  - Cách dùng X-Api-Key thay cho Bearer token
  - API key lookup & UserPermissionContext extraction
  - Python, JavaScript, cURL examples
  - Permission validation & filtering

- **[TEAMS_ENDPOINT_AUTHENTICATION.md](TEAMS_ENDPOINT_AUTHENTICATION.md)** - Sử dụng `/teams/{id}/runs` (10 phút)
  - ❌ Không thể pass token/API-key vào user_id
  - ✅ Solution 1: Dùng `/hitc/chat` (recommended)
  - ✅ Solution 2: Wrap endpoint với authentication
  - Why & how so sánh

- **[MCP_TOOL_TEMPLATE.md](MCP_TOOL_TEMPLATE.md)** - Implement MCP tools (25 phút)
  - Pattern: Extract Context → Validate → Query → Filter
  - Hai complete tool examples với permission checks
  - Security best practices
  - Unit test examples

- **[TEST_XAPIKEY.md](TEST_XAPIKEY.md)** - Test & Debug (20 phút)
  - Setup test API key
  - Test cases (basic, invalid, session, streaming, permissions)
  - Verify context in session
  - Debug logging & troubleshooting

- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - Tóm tắt (10 phút)
  - Những gì đã làm
  - Complete system architecture
  - Key components
  - Next steps

### System Flow

```
HTTP Request (với Auth)
  ↓
Route Layer (extract UserPermissionContext)
  ↓
Workflow: chat_with_hitc(query, user, session_id)
  ↓
Team Handler: save_context + inject_into_agents + augment_query
  ↓
Agent: invoke MCP tools with augmented query
  ↓
MCP Tool: get_context(session_id) → validate → filter results
  ↓
Response (only authorized data for user)
```

## Deploy cùng modata-mcp

Nếu muốn chạy cả 2 project cùng Docker network:

```yaml
# docker-compose.override.yml (tại modata-agent)
services:
  api:
    environment:
      MCP_GATEWAY_URL: http://mcp-gateway:8001/sse
    networks:
      - modata-mcp-network

networks:
  modata-mcp-network:
    external: true
    name: modata-mcp-network
```
