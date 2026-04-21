"""
Authentication middleware for HITC AgentOS.

Supports:
- Bearer token (JWT) authentication via Keycloak
- X-Api-Key authentication via API key store

Injects user context into request state for route handlers and AgentOS.
"""

import logging
from typing import Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.middleware.permission import (
    UserPermissionContext,
    build_user_context_from_token,
    build_user_context_from_api_key,
)

logger = logging.getLogger(__name__)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Authenticate requests using X-Api-Key or Bearer token.
    
    Flow:
    1. Check if route is excluded (skip auth for /docs, /health, etc.)
    2. Extract X-Api-Key or Authorization header
    3. Validate credentials and build UserPermissionContext
    4. Inject into request.state for downstream handlers
    5. Pass to next middleware/route
    """
    
    def __init__(self, app, excluded_routes: Optional[list[str]] = None):
        super().__init__(app)
        self.excluded_routes = excluded_routes or [
            "/docs",
            "/redoc",
            "/openapi.json",
            "/health",
            "/metrics",
        ]
    
    async def dispatch(self, request: Request, call_next):
        # Skip authentication for excluded routes
        if self._is_excluded_route(request.url.path):
            logger.debug(f"Skipping auth for excluded route: {request.url.path}")
            return await call_next(request)
        
        try:
            # Extract authentication credentials
            auth_header = request.headers.get("authorization", "").strip()
            x_api_key = request.headers.get("x-api-key", "").strip()
            
            user: Optional[UserPermissionContext] = None
            auth_method: str = "none"
            
            # Try Bearer token first
            if auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "", 1).strip()
                if token:
                    logger.debug("Authenticating with Bearer token")
                    user = await build_user_context_from_token(token)
                    auth_method = "bearer"
            
            # Fall back to X-Api-Key
            elif x_api_key:
                logger.debug("Authenticating with X-Api-Key")
                user = await build_user_context_from_api_key(x_api_key)
                auth_method = "api-key"
            
            # No credentials provided
            if not user:
                logger.warning(
                    f"Authentication failed for {request.method} {request.url.path} - "
                    f"Missing credentials (auth_method={auth_method})"
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "Authentication required",
                        "auth_methods": [
                            "Authorization: Bearer <jwt_token>",
                            "X-Api-Key: <api_key>",
                        ],
                    },
                )
            
            # Inject user context into request state
            request.state.user = user
            request.state.user_id = user.user_id
            request.state.username = user.username
            request.state.auth_method = auth_method
            
            # Extract session_id from query params or body
            session_id = request.query_params.get("session_id", "")
            if not session_id and request.method in ["POST", "PUT"]:
                try:
                    body = await request.body()
                    if body:
                        import json
                        data = json.loads(body)
                        session_id = data.get("session_id", "")
                        # Re-wrap body for route handler
                        async def receive():
                            return {"type": "http.request", "body": body}
                        request._receive = receive
                except Exception as e:
                    logger.debug(f"Could not extract session_id from body: {e}")
            
            request.state.session_id = session_id
            
            # For AgentOS parameter injection - create dependencies dict
            request.state.dependencies = {
                "user_id": user.user_id,
                "username": user.username,
                "accessible_instances": user.accessible_instance_names,
                "company_code": user.company_code,
            }
            
            logger.info(
                f"User authenticated: {user.user_id} ({user.username}) "
                f"via {auth_method} for {request.method} {request.url.path}"
            )
            
        except Exception as e:
            # Log and return error response
            logger.error(
                f"Authentication error for {request.method} {request.url.path}: {type(e).__name__}: {str(e)}"
            )
            
            # Check if it's a known authentication error
            status_code = 401
            detail = str(e)
            
            if hasattr(e, 'status_code'):
                status_code = e.status_code
            if hasattr(e, 'detail'):
                detail = e.detail
            
            return JSONResponse(
                status_code=status_code,
                content={"detail": detail},
            )
        
        # Continue to next middleware/route
        response = await call_next(request)
        return response
    
    def _is_excluded_route(self, path: str) -> bool:
        """
        Check if route should skip authentication.
        
        Supports wildcard patterns:
        - /health (exact match)
        - /static/* (prefix with wildcard)
        """
        for excluded in self.excluded_routes:
            if excluded.endswith("*"):
                # Wildcard prefix match
                prefix = excluded.rstrip("*")
                if path.startswith(prefix):
                    return True
            else:
                # Exact match
                if path == excluded or path.startswith(excluded + "/"):
                    return True
        
        return False
