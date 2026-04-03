"""app/core/config.py — Config cho Agent project"""
from functools import lru_cache
from typing import Optional

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────
    APP_NAME: str  = "MODATA Agent"
    DEBUG:    bool = False
    LOG_LEVEL: str = "INFO"

    # ── PostgreSQL (session store) ────────────────────────────
    PG_HOST:     str = "localhost"
    PG_PORT:     int = 5432
    PG_USER:     str = "admin"
    PG_PASSWORD: str = "change_me"
    PG_DATABASE: str = "vectordb"

    @computed_field
    @property
    def PG_DSN(self) -> str:
        return (
            f"postgresql://{self.PG_USER}:{self.PG_PASSWORD}"
            f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DATABASE}"
        )

    # ── LLM server (remote, OpenAI-compatible) ────────────────
    LLM_BASE_URL:        str   = "http://192.168.100.114:8088"
    LLM_API_KEY:         str   = ""
    LLM_MODEL:           str   = "qwen3-8b"
    LLM_MAX_TOKENS:      int   = 1024
    LLM_TEMPERATURE:     float = 0.7
    LLM_TIMEOUT:         int   = 60
    LLM_ENABLE_THINKING: bool  = False

    # ── Keycloak ──────────────────────────────────────────────
    KEYCLOAK_URL:       str = "https://sso.hitc.vn/auth"
    KEYCLOAK_REALM:     str = "vtqt"
    KEYCLOAK_CLIENT_ID: str = "vtqt"

    @computed_field
    @property
    def KEYCLOAK_JWKS_URL(self) -> str:
        return (
            f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"
            "/protocol/openid-connect/certs"
        )

    @computed_field
    @property
    def KEYCLOAK_ISSUER(self) -> str:
        return f"{self.KEYCLOAK_URL}/realms/{self.KEYCLOAK_REALM}"

    # ── MongoDB (chỉ dùng cho permission lookup) ──────────────
    MONGO_URI:      str = "mongodb://localhost:27017"
    MONGO_DATABASE: str = "generic_instance_v2"

    MONGO_COL_NHAN_VIEN:     str = "instance_data_thong_tin_nhan_vien"
    MONGO_COL_PHAN_QUYEN:    str = "instance_data_danh_sach_phan_quyen_chuc_nang"
    MONGO_COL_SYS_CONF_VIEW: str = "instance_data_sys_conf_view"

    # ── Redis (permission store + session cache) ─────────────
    REDIS_HOST:     str           = "localhost"
    REDIS_PORT:     int           = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB:       int           = 0
    # TTL quyền truy cập trong Redis (giây) — 8 giờ
    SESSION_PERM_TTL: int = 28800

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ── MCP Gateway (modata-mcp project) ─────────────────────
    MCP_GATEWAY_URL: str = "http://localhost:8001/sse"
    MCP_TIMEOUT:     int = 60

    # ── Security ──────────────────────────────────────────────
    INTERNAL_API_KEY: str = "change_me"

    # ── Session / history ────────────────────────────────────
    # Số turns inject vào context (lưu DB vẫn nhiều hơn để user scroll)
    RAG_MAX_HISTORY:  int = 3
    SESSION_TTL_DAYS: int = 30

    # ── Company ───────────────────────────────────────────────
    DEFAULT_COMPANY_CODE: str = "HITC"

    # ── AgentOS Control Plane ─────────────────────────────────
    AGENTOSAGNO_NAME: str = "HITC AgentOS"
    AGENTOSAGNO_DESCRIPTION: str = "Multi-Agent System for HITC workflows"
    AGENTOSAGNO_API_KEY: Optional[str] = None  # Control plane API key (if needed)
    AGENTOSAGNO_ENDPOINT: str = "http://localhost:8000"  # Where this AgentOS runs
    
    # ── AgentOS Database (PostgreSQL for Studio) ──────────────
    # Studio requires database to save/load agents, teams, workflows
    AGENTOSAGNO_DB_HOST: str = "localhost"
    AGENTOSAGNO_DB_PORT: int = 5432
    AGENTOSAGNO_DB_USER: str = "agno"
    AGENTOSAGNO_DB_PASSWORD: str = "agno_password"
    AGENTOSAGNO_DB_NAME: str = "agentosagno"
    
    @computed_field
    @property
    def AGENTOSAGNO_DB_URL(self) -> str:
        """PostgreSQL connection URL for AgentOS Studio"""
        return (
            f"postgresql+psycopg://{self.AGENTOSAGNO_DB_USER}:"
            f"{self.AGENTOSAGNO_DB_PASSWORD}@{self.AGENTOSAGNO_DB_HOST}:"
            f"{self.AGENTOSAGNO_DB_PORT}/{self.AGENTOSAGNO_DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()