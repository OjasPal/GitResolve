import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@dataclass(frozen=True)
class ModelConfig:
    provider: str  # "anthropic" | "google"
    model_id: str


@dataclass(frozen=True)
class PricingConfig:
    """Configurable cost per 1M tokens in USD."""
    prompt_per_million: float = 0.0
    completion_per_million: float = 0.0
    cache_read_per_million: float = 0.0


# Default pricing catalog (estimates per 1M tokens) - can be overridden via config
DEFAULT_PRICING: dict[str, PricingConfig] = {
    "claude-sonnet-5-5": PricingConfig(
        prompt_per_million=3.0,
        completion_per_million=15.0,
        cache_read_per_million=0.30,
    ),
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
    "claude-opus-5-5": PricingConfig(
        prompt_per_million=15.0,
        completion_per_million=75.0,
        cache_read_per_million=1.50,
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

    # Anthropic on Google Cloud
    anthropic_vertex_project_id: str = field(
        default_factory=lambda: os.getenv(
            "ANTHROPIC_VERTEX_PROJECT_ID",
            os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        )
    )
    anthropic_vertex_region: str = field(
        default_factory=lambda: os.getenv("ANTHROPIC_VERTEX_REGION", "global")
    )

    # Model hierarchy
    primary: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_PRIMARY_PROVIDER", "anthropic"),
            model_id=os.getenv("LLM_PRIMARY_MODEL", "claude-sonnet-5-5"),
        )
    )
    fallback_1: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_FALLBACK_1_PROVIDER", "google"),
            model_id=os.getenv("LLM_FALLBACK_1_MODEL", "gemini-4-argon"),
        )
    )
    fallback_2: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_FALLBACK_2_PROVIDER", "google"),
            model_id=os.getenv("LLM_FALLBACK_2_MODEL", "gemini-3.8-flash"),
        )
    )
    escalation: ModelConfig = field(
        default_factory=lambda: ModelConfig(
            provider=os.getenv("LLM_ESCALATION_PROVIDER", "anthropic"),
            model_id=os.getenv("LLM_ESCALATION_MODEL", "claude-opus-5-5"),
        )
    )

    request_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "180")))
    max_output_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192")))
    thinking_level: str = field(default_factory=lambda: os.getenv("LLM_THINKING_LEVEL", "LOW"))

    # Routing behavior
    max_provider_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_PROVIDER_RETRIES", "2"))
    )
    enable_failover: bool = field(
        default_factory=lambda: os.getenv("LLM_ENABLE_FAILOVER", "true").lower() in ("true", "1", "yes")
    )
    enable_opus_escalation: bool = field(
        default_factory=lambda: os.getenv("LLM_ENABLE_OPUS_ESCALATION", "true").lower() in ("true", "1", "yes")
    )
    max_opus_escalations: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_OPUS_ESCALATIONS", "1"))
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


def get_router_settings() -> RouterSettings:
    """Return fresh settings instance from environment variables."""
    return RouterSettings()
