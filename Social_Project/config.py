"""
Centralised configuration.

Reads from environment variables. Loads a local .env file in development so
running `python main.py` works without exporting variables manually. Railway
injects variables directly, so the .env load is a no-op there.
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- App ----
    app_name: str = "JACK Social Scheduler"
    app_version: str = "1.0.0"
    base_url: str = Field(default="http://localhost:8000")
    log_level: str = Field(default="INFO")

    # The wall-clock cadence at which the scheduler sweeps the queue.
    scheduler_interval_seconds: int = Field(default=30, ge=10, le=300)

    # ---- Database ----
    database_url: str = Field(default="")

    # ---- Admin auth (single-user, self-hosted) ----
    # A bcrypt hash of the admin password. Use scripts/hash_password.py to mint one.
    # If unset the UI is locked and only OAuth callbacks remain reachable.
    admin_password_hash: str = Field(default="")
    session_secret: str = Field(default="")
    session_ttl_hours: int = Field(default=24 * 14, ge=1)

    # ---- Meta (Threads + Instagram share the same app credentials) ----
    meta_app_id: str = Field(default="")
    meta_app_secret: str = Field(default="")
    meta_graph_version: str = Field(default="v21.0")

    # ---- TikTok ----
    tiktok_client_key: str = Field(default="")
    tiktok_client_secret: str = Field(default="")

    # ---- CORS ----
    cors_origins: str = Field(default="")

    # ---- Optional integrations (Deliverables 3, 5, 6) ----
    # SMTP for token-expiry alerts. All-or-nothing: if any one is missing, alerts are disabled.
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = Field(default="")
    smtp_pass: str = Field(default="")
    smtp_from: str = Field(default="")
    alert_email: str = Field(default="")

    # Substack RSS fan-out (Deliverable 5). Comma-separated feed URLs.
    substack_feeds: str = Field(default="")

    # Ollama for /repurpose rewrites (Deliverable 6). Optional, default off.
    ollama_base_url: str = Field(default="")
    ollama_model: str = Field(default="llama3.2")

    # Retention default for /settings purge (Deliverable 13).
    retention_default_days: int = Field(default=365, ge=1, le=10000)

    # Circuit breaker (Deliverable 2): pause a platform after N consecutive failures.
    circuit_breaker_threshold: int = Field(default=3, ge=1, le=100)
    circuit_breaker_cooldown_seconds: int = Field(default=900, ge=30, le=86400)

    # Skip APScheduler startup in tests / one-shot CLI tools.
    disable_scheduler: bool = Field(default=False)

    @field_validator("database_url")
    @classmethod
    def _normalise_pg_url(cls, v: str) -> str:
        if not v:
            return v
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Neon/Replit inject libpq-style query params (sslmode, channel_binding)
        # that asyncpg rejects with "connect() got an unexpected keyword
        # argument 'sslmode'". SSL is enforced via connect_args ssl=True in
        # database.py, so strip these from the async URL. Alembic reads the raw
        # DATABASE_URL through psycopg2, which understands them, and is unaffected.
        if "+asyncpg" in v and "?" in v:
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

            parts = urlsplit(v)
            kept = [
                (k, val)
                for k, val in parse_qsl(parts.query, keep_blank_values=True)
                if k not in {"sslmode", "channel_binding"}
            ]
            v = urlunsplit(parts._replace(query=urlencode(kept)))
        return v

    @field_validator("base_url")
    @classmethod
    def _normalise_base_url(cls, v: str) -> str:
        """Strip trailing slash; reject obviously malformed schemes. F-50, F-51."""
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("BASE_URL must start with http:// or https://")
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """F-58: reject typos before logging.setLevel crashes the app."""
        up = v.upper()
        if up not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL, got {v!r}")
        return up

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.cors_origins.strip():
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.admin_password_hash and self.session_secret)

    @property
    def smtp_enabled(self) -> bool:
        """SMTP alerts require all six fields. Falsy if any missing. F-60."""
        return all(
            (
                self.smtp_host,
                self.smtp_user,
                self.smtp_pass,
                self.smtp_from,
                self.alert_email,
            )
        )

    @property
    def substack_feed_list(self) -> list[str]:
        if not self.substack_feeds.strip():
            return []
        return [u.strip() for u in self.substack_feeds.split(",") if u.strip()]

    @property
    def ollama_enabled(self) -> bool:
        return bool(self.ollama_base_url.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
