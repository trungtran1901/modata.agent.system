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
