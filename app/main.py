"""
app/main.py — FastAPI entry point với HITC AgentOS (1 AgentOS duy nhất)

AgentOS Hierarchy:
  HitcAgentOS (duy nhất)
    ├── HRM Team       (nhân sự, chấm công, đơn từ)
    └── Document Team  (đọc hiểu văn bản, QA, trích xuất JSON)

Routes:
  /chat          — General agents (AgentOS cũ, giữ backward compat)
  /hrm/*         — HRM Team + OCR pipeline
  /hitc/*        — HITC AgentOS unified (HRM + Document)
  /health        — Health check
  /teams         — Tổng quan teams

Authentication:
  - Bearer token (JWT) via Authorization header
  - X-Api-Key via X-Api-Key header
  - Middleware injects UserPermissionContext into request.state
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.routes.routes import chat_router
from app.api.routes.hrm_routes import hrm_router
from app.api.routes.hitc_routes import hitc_router          # ← HITC unified
from app.middleware.auth_middleware import AuthenticationMiddleware  # ← Authentication
from workflow.hitc_agent import create_hitc_agent_os_app    # ← Single AgentOS

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    description="""
## MODATA Agent System — HITC AgentOS

### 🤖 General Chat — `/chat`
Chat đa mục đích (backward compat). Auto-route đến agents hiện có.

### 👥 HRM Team — `/hrm/chat`
Team chuyên nhân sự: nhân viên, nghỉ phép, chấm công, đơn từ, bảng công.
OCR tờ trình hành chính: `/hrm/ocr` và `/hrm/ocr/stream`.

### 🏢 HITC AgentOS — `/hitc/chat`
**Single AgentOS** chứa tất cả teams:
- **HRM Team** — nhân sự
- **Document Intelligence Team** — đọc hiểu văn bản, QA, trích xuất JSON

Endpoint thống nhất:
- `POST /hitc/chat` — auto-detect team từ query
- `POST /hitc/document/chat` — Document Team (nhận document_content, output_schema, role)
- `GET  /hitc/teams` — danh sách teams

### 📄 Document Intelligence — `/hitc/document/chat`
Xử lý văn bản linh hoạt:
- Tóm tắt, QA về nội dung văn bản
- Trích xuất JSON theo schema người dùng định nghĩa
- Làm giàu dữ liệu qua MCP (tra nhân viên, phòng ban)
    """,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Authentication Middleware (X-Api-Key + Bearer Token) ─────
# IMPORTANT: Register BEFORE routes to intercept all requests
app.add_middleware(
    AuthenticationMiddleware,
    excluded_routes=[
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    ]
)
logger.info("✓ Authentication middleware registered (X-Api-Key + Bearer token)")



# ── JSON error handler ────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    errors = exc.errors()
    for error in errors:
        if error.get("type") == "json_invalid":
            ctx       = error.get("ctx", {})
            error_msg = ctx.get("error", str(error))
            logger.warning("JSON error from %s: %s", request.client, error_msg)
            if "trailing comma" in error_msg.lower():
                return JSONResponse(status_code=400, content={
                    "error":           "Invalid JSON format",
                    "message":         "Your JSON has a trailing comma. Remove it and try again.",
                    "example_wrong":   '{"query": "test", "session_id": "s1",}',
                    "example_correct": '{"query": "test", "session_id": "s1"}',
                })
    return JSONResponse(status_code=422, content={"detail": errors})


# ── Routers ───────────────────────────────────────────────────
app.include_router(chat_router)     # /chat (backward compat)
app.include_router(hrm_router)      # /hrm/*
app.include_router(hitc_router)     # /hitc/*


# ── System endpoints ──────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/teams", tags=["System"])
def list_teams():
    """Tổng quan hệ thống HITC AgentOS."""
    return {
        "agentos":       "HITC AgentOS (Single)",
        "control_plane": settings.AGENTOSAGNO_ENDPOINT,
        "teams": [
            {
                "name":        "HRM Team",
                "chat_url":    "POST /hrm/chat",
                "hitc_url":    "POST /hitc/chat (force_team=hrm)",
                "description": "Nhân sự — nhân viên, chấm công, đơn từ, nghỉ phép",
            },
            {
                "name":        "Document Intelligence Team",
                "chat_url":    "POST /hitc/document/chat",
                "hitc_url":    "POST /hitc/chat (force_team=document)",
                "description": "Đọc hiểu văn bản — tóm tắt, QA, trích xuất JSON",
            },
        ],
        "pipelines": {
            "ocr_to_trinh": {
                "url":        "POST /hrm/ocr",
                "stream_url": "POST /hrm/ocr/stream",
                "description": "OCR tờ trình hành chính → JSON chuẩn (pipeline 3 bước)",
            },
        },
        "legacy": {
            "general_chat": "POST /chat",
        },
    }


# ── HITC AgentOS (Single — thay thế các AgentOS riêng lẻ) ────
logger.info("Integrating HITC AgentOS (HRM Team + Document Intelligence Team)...")
app = create_hitc_agent_os_app(base_app=app)
logger.info("✓ HITC AgentOS integrated")