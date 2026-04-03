"""app/main.py — FastAPI entry point with AgentOS integration"""
import json
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.routes.routes import chat_router
from workflow.agents import create_agent_os_app

logger = logging.getLogger(__name__)

app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Custom Exception Handler for JSON errors ──────────────────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """Handle validation errors with helpful messages."""
    errors = exc.errors()
    
    # Check if it's a JSON decode error
    for error in errors:
        if error.get("type") == "json_invalid":
            ctx = error.get("ctx", {})
            error_msg = ctx.get("error", str(error))
            
            logger.warning(f"JSON error from {request.client}: {error_msg}")
            
            # Check if it's trailing comma error
            if "trailing comma" in error_msg.lower():
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Invalid JSON format",
                        "message": "Your JSON has a trailing comma. Remove it and try again.",
                        "example_wrong": '{"query": "test", "session_id": "s1",}',
                        "example_correct": '{"query": "test", "session_id": "s1"}',
                        "details": error_msg,
                    }
                )
    
    # For other validation errors, return standard response
    return JSONResponse(
        status_code=422,
        content={"detail": errors}
    )


# ── Include chat router ────────────────────────────────────────
app.include_router(chat_router)


# ── Health check endpoint ─────────────────────────────────────
@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "modata-agent-system"}


# ── Integrate AgentOS ─────────────────────────────────────────
# AgentOS will add:
#   GET  /agents/           - List all agents
#   POST /agents/{id}/runs  - Run specific agent
#   GET  /monitor           - Monitoring dashboard
#   etc.
logger.info("Integrating AgentOS with base app...")
app = create_agent_os_app(base_app=app)
logger.info("✓ AgentOS integrated successfully")
