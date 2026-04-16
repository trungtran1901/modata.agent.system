"""
utils/permission.py

Flow xác thực hỗ trợ 2 phương thức:

Phương thức 1 — Bearer JWT (Keycloak):
  Header: Authorization: Bearer <token>
  1. Verify JWT → lấy preferred_username
  2. Tra MongoDB instance_data_thong_tin_nhan_vien → thông tin nhân viên
  3. Tra MongoDB instance_data_danh_sach_phan_quyen_chuc_nang → lọc chức năng
  4. Tra MongoDB instance_data_sys_conf_view → map ma_chuc_nang → instance_name

Phương thức 2 — API Key:
  Header: X-Api-Key: <api_key>
  1. Tra MongoDB instance_data_danh_sach_api_key → xác thực api_key, lấy ten_dang_nhap
  2. Kiểm tra is_active, is_deleted, ngay_het_han_token
  3. Tra MongoDB instance_data_thong_tin_nhan_vien theo ten_dang_nhap → thông tin nhân viên
  4. Tiếp tục flow phân quyền giống Bearer JWT (bước 3, 4 ở trên)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from jose import jwt, JWTError
import httpx

from app.core.config import settings
from app.db.mongo import get_db

logger = logging.getLogger(__name__)

# Tên collection chứa danh sách API Key
_COL_API_KEY = "instance_data_danh_sach_api_key"


# ─────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────

@dataclass
class UserPermissionContext:
    # Từ Keycloak JWT hoặc API Key record
    user_id:  str
    username: str
    email:    str
    roles:    list[str]

    # Từ collection nhan_vien
    company_code:      str           = "HITC"
    don_vi_code:       str           = ""
    don_vi_path:       str           = ""
    vi_tri_cong_viec:  Optional[str] = None
    nhan_vien_vai_tro: list[str]     = field(default_factory=list)

    # Kết quả phân quyền
    accessible_ma_chuc_nang:   set[str]            = field(default_factory=set)
    # {instance_name: [ma_chuc_nang, ...]} — format mới cho view-based permission
    accessible_instance_names: dict[str, list[str]] = field(default_factory=dict)

    # Phương thức xác thực đã dùng (để debug / audit log)
    auth_method: str = "bearer"   # "bearer" | "api_key"


# ─────────────────────────────────────────────────────────────
# PERMISSION SERVICE
# ─────────────────────────────────────────────────────────────

class PermissionService:

    def __init__(self):
        self._jwks: dict | None = None

    # ── Keycloak JWT ──────────────────────────────────────────

    async def _get_jwks(self) -> dict:
        if not self._jwks:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(settings.KEYCLOAK_JWKS_URL)
                r.raise_for_status()
                self._jwks = r.json()
        return self._jwks

    async def verify_token(self, bearer: str) -> dict:
        token = bearer.removeprefix("Bearer ").strip()
        try:
            payload = jwt.decode(
                token,
                await self._get_jwks(),
                algorithms=["RS256"],
                issuer=settings.KEYCLOAK_ISSUER,
                options={"verify_aud": False},
            )
            return payload
        except JWTError as e:
            raise PermissionError(f"Invalid token: {e}")

    # ── MongoDB: xác thực API Key ─────────────────────────────

    @staticmethod
    def _verify_api_key(api_key: str) -> str:
        """
        Tra collection instance_data_danh_sach_api_key, kiểm tra:
          - api_key khớp
          - is_deleted != true
          - is_active == true
          - ngay_het_han_token chưa quá hạn (hoặc null = không giới hạn)

        Trả về ten_dang_nhap nếu hợp lệ, raise PermissionError nếu không.
        """
        doc = get_db()[_COL_API_KEY].find_one(
            {
                "api_key":    api_key,
                "is_deleted": {"$ne": True},
                "is_active":  {"$ne": False},
            },
            {
                "ten_dang_nhap": 1,
                "ho_va_ten":     1,
                "email":         1,
                "company_code":  1,
                "ngay_het_han_token": 1,
            },
        )

        if not doc:
            raise PermissionError("API Key không hợp lệ hoặc đã bị vô hiệu hoá")

        # Kiểm tra ngày hết hạn (nếu có)
        expiry = doc.get("ngay_het_han_token")
        if expiry is not None:
            # Chuẩn hoá: MongoDB có thể trả datetime naive hoặc aware
            if isinstance(expiry, datetime):
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if datetime.now(tz=timezone.utc) > expiry:
                    raise PermissionError("API Key đã hết hạn")

        username = doc.get("ten_dang_nhap", "").strip()
        if not username:
            raise PermissionError("API Key không liên kết với tài khoản hợp lệ")

        logger.info("API Key OK — ten_dang_nhap: %s", username)
        return username

    # ── MongoDB: lấy thông tin nhân viên ─────────────────────

    @staticmethod
    def _get_nhan_vien(username: str) -> dict | None:
        return get_db()[settings.MONGO_COL_NHAN_VIEN].find_one(
            {"ten_dang_nhap": username, "is_deleted": {"$ne": True}},
            {
                "_id": 1, "ten_dang_nhap": 1, "email": 1, "company_code": 1,
                "don_vi_cong_tac": 1, "path_don_vi_cong_tac": 1,
                "ds_don_vi_cong_tac": 1, "vi_tri_cong_viec": 1,
                "vai_tro": 1, "phong_ban_phu_trach": 1,
            },
        )

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_nv_dv(nv: dict) -> tuple[set[str], set[str]]:
        codes: set[str] = set()
        paths: set[str] = set()
        dv = nv.get("don_vi_cong_tac", {})
        if isinstance(dv, dict):
            code = dv.get("option", {}).get("code") or dv.get("value", "")
            if code:
                codes.add(code)
        path = nv.get("path_don_vi_cong_tac", "")
        if path:
            paths.add(path)
        for item in (nv.get("ds_don_vi_cong_tac") or []):
            for ov in (item.get("objectValue") or []):
                if ov.get("key") == "code" and ov.get("value"):
                    codes.add(ov["value"])
                if ov.get("key") == "path" and ov.get("value"):
                    paths.add(ov["value"])
        return codes, paths

    @staticmethod
    def _extract_pq_dv(pq: dict, field_name: str) -> tuple[set[str], set[str]]:
        codes: set[str] = set()
        paths: set[str] = set()
        for item in (pq.get(field_name) or []):
            if not isinstance(item, dict):
                continue
            codes.add(item.get("value", ""))
            for ov in (item.get("objectValue") or []):
                if ov.get("key") == "path" and ov.get("value"):
                    paths.add(ov["value"])
        return codes, paths

    @staticmethod
    def _path_match(nv_paths: set[str], pq_paths: set[str]) -> bool:
        return any(
            nv_path.startswith(pq_path)
            for nv_path in nv_paths
            for pq_path in pq_paths
        )

    # ── MongoDB: danh sách ma_chuc_nang có quyền ─────────────

    def _get_accessible_chuc_nang(self, nv: dict) -> list[str]:
        username = nv.get("ten_dang_nhap", "")
        nv_vt    = {
            v.get("value", "") for v in (nv.get("vai_tro") or [])
            if isinstance(v, dict) and v.get("value")
        }
        nv_codes, nv_paths = self._extract_nv_dv(nv)

        pq_list = list(get_db()[settings.MONGO_COL_PHAN_QUYEN].find(
            {"is_deleted": {"$ne": True}, "is_active": {"$ne": False}},
            {
                "ma_chuc_nang": 1, "vai_tro": 1,
                "don_vi_cong_tac": 1, "phong_ban_phu_trach": 1,
                "danh_sach_nguoi_dung": 1,
            },
        ))

        result: list[str] = []
        for pq in pq_list:
            ma = pq.get("ma_chuc_nang", "")
            if not ma:
                continue

            ds_users = {
                u.get("value", "") for u in (pq.get("danh_sach_nguoi_dung") or [])
                if isinstance(u, dict)
            }
            if username in ds_users:
                result.append(ma)
                continue

            pq_vt = {v.get("value", "") for v in (pq.get("vai_tro") or []) if isinstance(v, dict)}
            if nv_vt & pq_vt:
                result.append(ma)
                continue

            pq_dv_codes, pq_dv_paths = self._extract_pq_dv(pq, "don_vi_cong_tac")
            if (nv_codes & pq_dv_codes) or self._path_match(nv_paths, pq_dv_paths):
                result.append(ma)
                continue

            pq_pb_codes, pq_pb_paths = self._extract_pq_dv(pq, "phong_ban_phu_trach")
            if (nv_codes & pq_pb_codes) or self._path_match(nv_paths, pq_pb_paths):
                result.append(ma)

        return result

    # ── MongoDB: map ma_chuc_nang → instance_name ────────────

    @staticmethod
    def _get_accessible_instances(ma_list: list[str]) -> dict[str, list[str]]:
        """
        Trả về {instance_name: [ma_chuc_nang, ...]}.
        modata-mcp dùng để load đúng field list theo view permission.
        """
        if not ma_list:
            return {}
        docs = get_db()[settings.MONGO_COL_SYS_CONF_VIEW].find(
            {
                "ma_chuc_nang": {"$in": ma_list},
                "is_deleted":   {"$ne": True},
                "is_active":    {"$ne": False},
            },
            {"instance_name": 1, "ma_chuc_nang": 1},
        )
        result: dict[str, list[str]] = {}
        for d in docs:
            inst = d.get("instance_name")
            ma   = d.get("ma_chuc_nang")
            if inst and ma:
                result.setdefault(inst, [])
                if ma not in result[inst]:
                    result[inst].append(ma)
        return result

    # ── Shared: build context từ username ────────────────────

    def _build_context_from_username(
        self,
        user_id:     str,
        username:    str,
        email:       str,
        roles:       list[str],
        auth_method: str,
    ) -> UserPermissionContext:
        """
        Dùng chung cho cả Bearer JWT và API Key sau khi đã có username.
        Tra nhân viên → phân quyền → trả UserPermissionContext.
        """
        nv = self._get_nhan_vien(username)
        if not nv:
            logger.warning("Không tìm thấy nhân viên: %s", username)
            return UserPermissionContext(
                user_id=user_id, username=username,
                email=email, roles=roles,
                auth_method=auth_method,
            )

        dv          = nv.get("don_vi_cong_tac") or {}
        don_vi_code = (
            (dv.get("option") or {}).get("code") or dv.get("value", "")
        ) if isinstance(dv, dict) else ""
        don_vi_path = nv.get("path_don_vi_cong_tac") or ""
        vi_tri      = nv.get("vi_tri_cong_viec")
        company     = nv.get("company_code") or settings.DEFAULT_COMPANY_CODE
        nv_vai_tro  = [
            v.get("value", "") for v in (nv.get("vai_tro") or [])
            if isinstance(v, dict) and v.get("value")
        ]

        ma_list   = self._get_accessible_chuc_nang(nv)
        instances = self._get_accessible_instances(ma_list)

        logger.info(
            "User %s (%s) → %d chức năng, %d collections",
            username, auth_method, len(ma_list), len(instances),
        )

        return UserPermissionContext(
            user_id=user_id, username=username, email=email, roles=roles,
            company_code=company, don_vi_code=don_vi_code, don_vi_path=don_vi_path,
            vi_tri_cong_viec=vi_tri, nhan_vien_vai_tro=nv_vai_tro,
            accessible_ma_chuc_nang=set(ma_list),
            accessible_instance_names=instances,
            auth_method=auth_method,
        )

    # ── Entry point: Bearer JWT ───────────────────────────────

    async def build_context(self, bearer: str) -> UserPermissionContext:
        """Xác thực Bearer JWT → build UserPermissionContext."""
        payload  = await self.verify_token(bearer)
        username = payload.get("preferred_username", "")
        user_id  = payload.get("sub", "")
        email    = payload.get("email", "")
        roles    = payload.get("realm_access", {}).get("roles", [])

        logger.info("Token OK — user: %s | roles: %s", username, roles)

        return self._build_context_from_username(
            user_id=user_id,
            username=username,
            email=email,
            roles=roles,
            auth_method="bearer",
        )

    # ── Entry point: API Key ──────────────────────────────────

    def build_context_from_api_key(self, api_key: str) -> UserPermissionContext:
        """
        Xác thực X-Api-Key → build UserPermissionContext.
        Đồng bộ (không async) vì chỉ dùng MongoDB, không cần HTTP call.
        """
        username = self._verify_api_key(api_key)

        # API Key không có JWT payload → dùng ten_dang_nhap làm user_id
        return self._build_context_from_username(
            user_id=username,
            username=username,
            email="",       # sẽ được lấy từ bảng nhân viên nếu có
            roles=[],       # API Key không có Keycloak roles
            auth_method="api_key",
        )