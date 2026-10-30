"""Typed settings. Secrets come from the environment (Secret Manager in prod)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False
    )

    # --- Runtime -----------------------------------------------------------
    environment: Literal["local", "test", "production"] = "local"
    store_backend: Literal["memory", "firestore"] = "memory"
    agent_mode: Literal["fake", "langgraph"] = "fake"
    # Local convenience: run the worker loop inside the API process. Only honoured
    # when environment is local/test; use `python -m app.worker.runner` otherwise.
    embedded_worker: bool = False
    cors_origins: list[str] = ["http://localhost:3000"]
    # /docs, /redoc and /openapi.json: on locally, off in production unless set explicitly.
    docs_enabled: bool | None = None
    log_format: Literal["auto", "json", "text"] = "auto"  # auto = json in production
    # Number of trusted reverse proxies in front of the API (Cloud Run: 1). 0 = ignore
    # X-Forwarded-For. The client IP is taken that many entries from the right, so a
    # client cannot spoof it by sending its own header.
    trusted_proxy_hops: int = 0

    # --- Firestore (non-secret) -------------------------------------------
    gcp_project: str | None = None
    firestore_database: str = "(default)"

    # --- GitHub App (secrets are supplied by env/Secret Manager) ------------
    github_app_id: int | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: str | None = None
    github_app_private_key: str | None = None
    github_app_callback_url: str = "http://localhost:8000/api/v1/auth/github/callback"
    frontend_base_url: str = "http://localhost:3000"

    # --- Auth ---------------------------------------------------------------
    session_jwt_secret: str = Field(default="dev-only-insecure-secret-change-me-32b")
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60

    # --- Request guards -----------------------------------------------------
    max_body_bytes: int = 64 * 1024
    rate_limit_per_minute_ip: int = 300  # every request except /health; 0 disables
    rate_limit_writes_per_minute_user: int = 30  # POST actions per user; 0 disables

    # --- Quotas and task limits (TRD sections 4 and 5) ----------------------
    max_tasks_per_day: int = 10
    max_repair_attempts: int = 3
    max_model_calls: int = 25
    task_timeout_seconds: int = 20 * 60
    execution_timeout_seconds: int = 300
    max_diff_files: int = 5
    max_diff_lines: int = 150

    # --- Worker lease (Backend Schema, section 11) --------------------------
    lease_seconds: int = 90
    heartbeat_seconds: int = 30
    reaper_interval_seconds: int = 60
    poll_interval_seconds: int = 5

    # --- Retention (Backend Schema, section 9) ------------------------------
    task_retention_days: int = 90
    subdoc_retention_days: int = 30

    def assert_safe_for_production(self) -> None:
        if self.environment != "production":
            return
        if self.session_jwt_secret.startswith("dev-only") or len(
            self.session_jwt_secret
        ) < 32:
            raise RuntimeError("SESSION_JWT_SECRET must be a strong secret in production.")
        if self.store_backend != "firestore":
            raise RuntimeError("Production requires STORE_BACKEND=firestore.")
        if not all(
            (
                self.github_app_id,
                self.github_app_client_id,
                self.github_app_client_secret,
                self.github_app_private_key,
            )
        ):
            raise RuntimeError("GitHub App configuration is required in production.")
        if not self.gcp_project:
            raise RuntimeError("GCP_PROJECT is required in production.")
        if not self.github_app_callback_url.startswith("https://"):
            raise RuntimeError("GitHub OAuth callback URL must use HTTPS in production.")
        if not self.frontend_base_url.startswith("https://"):
            raise RuntimeError("FRONTEND_BASE_URL must use HTTPS in production.")
        if not self.cors_origins or "*" in self.cors_origins:
            raise RuntimeError("Production CORS origins must be explicit.")

    def assert_safe_for_worker(self) -> None:
        """Production check for the worker: it never needs the session or OAuth secrets."""
        if self.environment != "production":
            return
        if self.store_backend != "firestore" or not self.gcp_project:
            raise RuntimeError("Production worker requires STORE_BACKEND=firestore and GCP_PROJECT.")
        if not (self.github_app_id and self.github_app_private_key):
            raise RuntimeError("Production worker requires GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY.")

    @property
    def docs_on(self) -> bool:
        return self.docs_enabled if self.docs_enabled is not None else self.environment != "production"

    @property
    def log_json(self) -> bool:
        if self.log_format == "auto":
            return self.environment == "production"
        return self.log_format == "json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
