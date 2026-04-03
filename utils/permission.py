"""
utils/permission.py

Flow:
1. Verify Keycloak Bearer JWT → lấy preferred_username
2. Tra MongoDB instance_data_thong_tin_nhan_vien → lấy thông tin nhân viên
3. Tra MongoDB instance_data_danh_sach_phan_quyen_chuc_nang → lọc chức năng có quyền
4. Tra MongoDB instance_data_sys_conf_view → map ma_chuc_nang → instance_name
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from jose import jwt, JWTError
import httpx

from app.core.config import settings
from app.db.mongo import get_db

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────

@dataclass
class UserPermissionContext:
    # Từ Keycloak JWT
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

    # ── Entry point ───────────────────────────────────────────

    async def build_context(self, bearer: str) -> UserPermissionContext:
        payload  = await self.verify_token(bearer)
        username = payload.get("preferred_username", "")
        user_id  = payload.get("sub", "")
        email    = payload.get("email", "")
        roles    = payload.get("realm_access", {}).get("roles", [])

        logger.info("Token OK — user: %s | roles: %s", username, roles)

        nv = self._get_nhan_vien(username)
        if not nv:
            logger.warning("Không tìm thấy nhân viên: %s", username)
            return UserPermissionContext(
                user_id=user_id, username=username, email=email, roles=roles
            )

        dv          = nv.get("don_vi_cong_tac") or {}
        don_vi_code = (
            (dv.get("option") or {}).get("code") or dv.get("value", "")
        ) if isinstance(dv, dict) else ""
        don_vi_path    = nv.get("path_don_vi_cong_tac") or ""
        vi_tri         = nv.get("vi_tri_cong_viec")
        company        = nv.get("company_code") or settings.DEFAULT_COMPANY_CODE
        nv_vai_tro     = [
            v.get("value", "") for v in (nv.get("vai_tro") or [])
            if isinstance(v, dict) and v.get("value")
        ]

        ma_list   = self._get_accessible_chuc_nang(nv)
        instances = self._get_accessible_instances(ma_list)

        logger.info(
            "User %s → %d chức năng, %d collections",
            username, len(ma_list), len(instances),
        )

        return UserPermissionContext(
            user_id=user_id, username=username, email=email, roles=roles,
            company_code=company, don_vi_code=don_vi_code, don_vi_path=don_vi_path,
            vi_tri_cong_viec=vi_tri, nhan_vien_vai_tro=nv_vai_tro,
            accessible_ma_chuc_nang=set(ma_list),
            accessible_instance_names=instances,  # dict[instance_name, list[ma]]
        )