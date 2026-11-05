import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class ModelConfig:
    provider: str  # "google"
    model_id: str


@dataclass(frozen=True)
class PricingConfig:
    """Configurable cost per 1M tokens in USD."""
    prompt_per_million: float = 0.0
    completion_per_million: float = 0.0
    cache_read_per_million: float = 0.0


# Default pricing catalog (estimates per 1M tokens) - can be overridden via config
DEFAULT_PRICING: dict[str, PricingConfig] = {
    "gemini-4-argon": PricingConfig(
        prompt_per_million=2.0,
        completion_per_million=8.0,
        cache_read_per_million=0.20,
    ),
    "gemini-3.8-flash": PricingConfig(
        prompt_per_million=0.15,
        completion_per_million=0.60,
        cache_read_per_million=0.0375,
    ),
}


@dataclass
class RouterSettings:
    # Google Cloud / Agent Platform
    google_cloud_project: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "")
    )
    google_cloud_location: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    )
    google_genai_use_enterprise: bool = field(
        default_factory=lambda: os.getenv("GOOGLE_GENAI_USE_ENTERPRISE", "true").lower() in ("true", "1", "yes")
    )

    # Model hierarchy
    primary: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_PRIMARY_PROVIDER", "google"),
            model_id=os.getenv("LLM_PRIMARY_MODEL", "gemini-3.8-flash"),
        )
    )
    fallback_1: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_FALLBACK_1_PROVIDER", "google"),
            model_id=os.getenv("LLM_FALLBACK_1_MODEL", "gemini-3.6-flash"),
        )
    )
    specialist: ModelConfig = field(default_factory=lambda: ModelConfig("google", os.getenv("LLM_SPECIALIST_MODEL", "gemini-4-argon")))
    specialist_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_SPECIALIST_TIMEOUT_SECONDS", "60")))

    request_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45")))
    max_output_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192")))
    thinking_level: str = field(default_factory=lambda: os.getenv("LLM_THINKING_LEVEL", "LOW"))

    # Routing behavior
    max_provider_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_PROVIDER_RETRIES", "1"))
    )
    enable_failover: bool = field(
        default_factory=lambda: os.getenv("LLM_ENABLE_FAILOVER", "true").lower() in ("true", "1", "yes")
    )
    # Retry timing (seconds)
    retry_initial_delay: float = field(
        default_factory=lambda: float(os.getenv("LLM_RETRY_INITIAL_DELAY", "0.5"))
    )
    retry_backoff_factor: float = field(
        default_factory=lambda: float(os.getenv("LLM_RETRY_BACKOFF_FACTOR", "2.0"))
    )
    retry_max_delay: float = field(
        default_factory=lambda: float(os.getenv("LLM_RETRY_MAX_DELAY", "8.0"))
    )

    def __post_init__(self):
        self.request_timeout_seconds = max(5, min(60, self.request_timeout_seconds))
        self.specialist_timeout_seconds = max(5, min(60, self.specialist_timeout_seconds))
        self.max_provider_retries = max(0, min(1, self.max_provider_retries))
        self.retry_max_delay = max(0, min(2, self.retry_max_delay))


def get_router_settings() -> RouterSettings:
    """Return fresh settings instance from environment variables."""
    return RouterSettings()
