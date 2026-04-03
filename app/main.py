"""app/main.py — FastAPI entry point với AgentOS + HRM Team"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.routes.routes import chat_router
from app.api.routes.hrm_routes import hrm_router       # ← HRM Team
from workflow.agents import create_agent_os_app

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    description="""
## MODATA Agent System

### 🤖 General Chat — `/chat`
Chat đa mục đích. Auto-route đến: CheckinAgent, DataQueryAgent,
AnalyticsAgent, EmailAgent, SearchDocsAgent.

### 👥 HRM Team — `/hrm/chat`
Team chuyên nhân sự với 2 agents:
- **Employee Agent** — thông tin nhân viên, tìm kiếm, danh sách, thâm niên
- **Leave Info Agent** — ngày nghỉ lễ, quy định nghỉ tuần, loại nghỉ phép

Endpoints nhanh (không qua LLM):
- `GET /hrm/holidays` — danh sách ngày nghỉ lễ
- `GET /hrm/leave-types` — loại nghỉ phép
- `GET /hrm/weekly-off-rules` — quy định nghỉ tuần
    """,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
app.include_router(chat_router)
app.include_router(hrm_router)     # /hrm/*


# ── System endpoints ──────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/teams", tags=["System"])
def list_teams():
    """Tổng quan các AI Teams trong hệ thống."""
    return {
        "teams": [
            {
                "name":        "HRM Team",
                "chat_url":    "POST /hrm/chat",
                "description": "Team nhân sự — thông tin nhân viên, nghỉ phép, ngày nghỉ lễ",
                "agents": [
                    {
                        "name":        "Employee Agent",
                        "id":          "hrm-employee-agent",
                        "speciality":  "Thông tin nhân viên, tìm kiếm, danh sách, thâm niên",
                        "tools":       [
                            "hrm_get_employee_info",
                            "hrm_search_employees",
                            "hrm_list_employees",
                            "tools_calculate_service_time",
                        ],
                        "collections": ["thong_tin_nhan_vien"],
                    },
                    {
                        "name":        "Leave Info Agent",
                        "id":          "hrm-leave-info-agent",
                        "speciality":  "Ngày nghỉ lễ, quy định nghỉ phép, loại nghỉ",
                        "tools":       [
                            "hrm_get_holidays",
                            "hrm_get_weekly_off_rules",
                            "hrm_get_leave_types",
                            "hrm_check_working_schedule",
                            "hrm_get_leave_policy_summary",
                        ],
                        "collections": [
                            "ngay_nghi_le",
                            "ngay_nghi_tuan",
                            "danh_sach_loai_nghi_phep",
                        ],
                    },
                ],
                "direct_endpoints": {
                    "GET /hrm/holidays":         "Ngày nghỉ lễ (không qua LLM)",
                    "GET /hrm/leave-types":      "Loại nghỉ phép (không qua LLM)",
                    "GET /hrm/weekly-off-rules": "Nghỉ tuần (không qua LLM)",
                },
            },
        ],
        "general_agents": {
            "chat_url":    "POST /chat",
            "description": "Chat đa mục đích — auto-route đến agent phù hợp",
            "agents":      [
                "CheckinAgent — chấm công, giờ vào ra",
                "DataQueryAgent — nhân viên, hợp đồng, phép, thiết bị",
                "AnalyticsAgent — thống kê, count, group by",
                "EmailAgent — gửi email, thông báo",
                "SearchDocsAgent — tài liệu nội bộ, quy định",
            ],
        },
    }


# ── AgentOS ───────────────────────────────────────────────────
logger.info("Integrating AgentOS...")
app = create_agent_os_app(base_app=app)
logger.info("✓ AgentOS integrated")