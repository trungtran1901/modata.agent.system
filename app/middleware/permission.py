"""
app/middleware/permission.py

Middleware để log/audit user requests trước khi gọi agent.

NOTE: Actual permission verification được xử lý bởi get_user() dependency
trong route handlers (check Authorization header hoặc X-Api-Key).

Middleware này chỉ:
  1. Log request từ user
  2. Track agent execution metrics
  3. Inject user context vào request.state (nếu có)
  4. Catch lỗi authorization từ routes

Also includes helper functions for authentication:
  - build_user_context_from_token() - JWT Bearer token
  - build_user_context_from_api_key() - X-Api-Key lookup
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional
import jwt

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


@dataclass
class UserPermissionContext:
    """User context with permissions from authentication."""
    user_id: str
    username: str
    accessible_instance_names: list[str]
    accessible_instances: dict[str, list[str]]  # {instance_name: [ma_chuc_nang]}
    company_code: str


async def build_user_context_from_token(token: str) -> UserPermissionContext:
    """
    Build UserPermissionContext from JWT Bearer token.
    
    Expected token claims:
    - sub: user_id
    - preferred_username: username
    - accessible_instances: {instance_name: [ma_chuc_nang]}
    - company_code: company code
    
    Raises:
    - HTTPException(401): Invalid or expired token
    """
    try:
        # For now, decode without verification to get claims
        # In production, verify signature with Keycloak public key
        unverified = jwt.decode(
            token,
            options={"verify_signature": False}
        )
        
        # Extract claims
        user_id = unverified.get("sub")
        username = unverified.get("preferred_username", unverified.get("name", ""))
        accessible_instances = unverified.get("accessible_instances", {})
        company_code = unverified.get("company_code", "")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: missing 'sub' claim")
        
        # Build context
        context = UserPermissionContext(
            user_id=user_id,
            username=username,
            accessible_instance_names=list(accessible_instances.keys()),
            accessible_instances=accessible_instances,
            company_code=company_code,
        )
        
        logger.debug(f"Built user context from JWT: {user_id}")
        return context
    
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        logger.error(f"Error building context from token: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed")


async def build_user_context_from_api_key(api_key: str) -> UserPermissionContext:
    """
    Build UserPermissionContext from X-Api-Key.
    
    Uses utils/permission.py PermissionService to:
    1. Verify API key in MongoDB (instance_data_danh_sach_api_key)
    2. Get username from API key record
    3. Get user info from nhan_vien collection
    4. Get accessible permissions from danh_sach_phan_quyen_chuc_nang
    
    Raises:
    - HTTPException(401): Invalid API key
    - HTTPException(500): Lookup service error
    """
    try:
        # Import here to avoid circular imports
        from utils.permission import PermissionService
        
        perm_svc = PermissionService()
        
        logger.debug(f"Verifying API key: {api_key[:20]}...")
        
        # Step 1: Verify API key → get username (this is synchronous)
        username = perm_svc._verify_api_key(api_key)
        logger.debug(f"API key verified for user: {username}")
        
        # Step 2: Get user info from nhan_vien (static method)
        nv = PermissionService._get_nhan_vien(username)
        if not nv:
            logger.warning(f"User not found in nhan_vien: {username}")
            raise HTTPException(status_code=401, detail="User not found")
        
        # Step 3: Get accessible ma_chuc_nang
        accessible_chuc_nang = perm_svc._get_accessible_chuc_nang(nv)
        logger.debug(f"User {username} has {len(accessible_chuc_nang)} functions")
        
        # Step 4: Map to instance names (static method)
        accessible_instances = PermissionService._get_accessible_instances(accessible_chuc_nang)
        
        # Build UserPermissionContext
        context = UserPermissionContext(
            user_id=str(nv.get("_id", "")),
            username=username,
            accessible_instance_names=list(accessible_instances.keys()),
            accessible_instances=accessible_instances,
            company_code=nv.get("company_code", "HITC"),
        )
        
        logger.info(f"User authenticated via API key: {username} ({context.user_id})")
        return context
    
    except HTTPException:
        raise
    except PermissionError as e:
        logger.warning(f"API key permission error: {str(e)}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Error building context from API key: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Authentication service error")


class PermissionMiddleware(BaseHTTPMiddleware):
    """
    Middleware để log/audit user requests đến agent endpoints.
    
    Theo dõi:
      - Request path, method, user info từ header
      - Response status và latency
      - User context injection (sau khi dependency verify)
    
    Các endpoint cần tracking:
      - /teams/* — Team execution
      - /agents/* — Agent execution
      - /hitc/* — HITC specific APIs
      - /hrm/* — HRM specific APIs
    """
    
    PROTECTED_PATHS = [
        "/teams/",
        "/agents/",
        "/hitc/",
        "/hrm/",
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Skip tracking cho health/docs/config
        if any(request.url.path.startswith(p) for p in ["/health", "/docs", "/openapi.json", "/config"]):
            return await call_next(request)
        
        # Log incoming request
        is_protected = any(request.url.path.startswith(p) for p in self.PROTECTED_PATHS)
        if is_protected:
            auth_header = request.headers.get("Authorization", "")
            api_key = request.headers.get("X-Api-Key", "")
            user_id = request.headers.get("X-User-ID", "")
            
            logger.debug(
                f"📥 Request: method={request.method} path={request.url.path} "
                f"auth={bool(auth_header)} api_key={bool(api_key)} user_id={user_id}"
            )
        
        # Process request
        start_time = time.time()
        try:
            response = await call_next(request)
            
            # Log response (permission check passed)
            if is_protected:
                duration = round((time.time() - start_time) * 1000, 2)
                logger.debug(
                    f"✓ Response: status={response.status_code} "
                    f"path={request.url.path} latency={duration}ms"
                )
            
            return response
        
        except Exception as e:
            # Permission error hoặc other error từ route
            duration = round((time.time() - start_time) * 1000, 2)
            logger.error(
                f"❌ Error: path={request.url.path} error={str(e)} "
                f"latency={duration}ms",
                exc_info=True
            )
            
            # Return error response
            return JSONResponse(
                status_code=500,
                content={"error": "Internal server error"}
            )
