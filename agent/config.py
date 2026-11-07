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


# Default pricing catalog (estimates per 1M tokens) - Google GenAI only
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
    "gemini-3.6-flash": PricingConfig(
        prompt_per_million=0.10,
        completion_per_million=0.40,
        cache_read_per_million=0.025,
    ),
}

# Per-node reasoning profiles and timeout ceilings (Phase 3):
# Fast-lane nodes always use LOW thinking and tight timeouts bounded by the 120s fast sub-deadline.
# Full-lane diagnosis, complex patch, Argon reflection, and independent review use MEDIUM/HIGH.
NODE_REASONING_PROFILES: dict[str, dict[str, Any]] = {
    "intake_triage": {"thinking_level": "LOW", "timeout_seconds": 15.0},
    "repo_profile": {"thinking_level": "LOW", "timeout_seconds": 15.0},
    "env_prepare": {"thinking_level": "LOW", "timeout_seconds": 20.0},
    "baseline_cache": {"thinking_level": "LOW", "timeout_seconds": 20.0},
    "fast_localize": {"thinking_level": "LOW", "timeout_seconds": 15.0},
    "fast_patch_action": {"thinking_level": "LOW", "timeout_seconds": 20.0},
    "fast_review": {"thinking_level": "LOW", "timeout_seconds": 20.0},
    "explore": {"thinking_level": "MEDIUM", "timeout_seconds": 35.0},
    "patch": {"thinking_level": "MEDIUM", "timeout_seconds": 45.0},
    "patch_retry": {"thinking_level": "HIGH", "timeout_seconds": 45.0},
    "specialist_reflect": {"thinking_level": "HIGH", "timeout_seconds": 60.0},
    "review": {"thinking_level": "HIGH", "timeout_seconds": 45.0},
}


def resolve_node_profile(
    node_name: str,
    *,
    lane: str = "full",
    attempt: int = 1,
    fast_remaining_seconds: float | None = None,
    task_remaining_seconds: float | None = None,
) -> tuple[str, float]:
    """Return (thinking_level, timeout_seconds) for a graph node, respecting lane and deadline bounds."""
    if lane == "fast" or node_name.startswith("fast_"):
        base_timeout = float(NODE_REASONING_PROFILES.get(node_name, {}).get("timeout_seconds", 20.0))
        if fast_remaining_seconds is not None and fast_remaining_seconds > 0:
            # Bound fast-lane per-call timeout so a hung primary can still fail over to fallback within the sub-deadline
            base_timeout = min(base_timeout, max(5.0, fast_remaining_seconds * 0.6))
        if task_remaining_seconds is not None and task_remaining_seconds > 0:
            base_timeout = min(base_timeout, max(5.0, task_remaining_seconds))
        return "LOW", round(base_timeout, 2)

    lookup_key = "patch_retry" if (node_name == "patch" and attempt > 1) else node_name
    spec = NODE_REASONING_PROFILES.get(lookup_key, {"thinking_level": "MEDIUM", "timeout_seconds": 45.0})
    thinking = str(spec["thinking_level"])
    timeout = float(spec["timeout_seconds"])
    if task_remaining_seconds is not None and task_remaining_seconds > 0:
        timeout = min(timeout, max(5.0, task_remaining_seconds))
    return thinking, round(timeout, 2)


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

    # Model hierarchy (Google GenAI only: 3.8-flash primary, 3.6-flash fallback, 4-argon specialist)
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
    specialist: ModelConfig = field(
        default_factory=lambda: ModelConfig("google", os.getenv("LLM_SPECIALIST_MODEL", "gemini-4-argon"))
    )
    specialist_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLM_SPECIALIST_TIMEOUT_SECONDS", "60"))
    )

    request_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "45"))
    )
    fast_request_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLM_FAST_REQUEST_TIMEOUT_SECONDS", "20"))
    )
    max_output_tokens: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192"))
    )
    thinking_level: str = field(
        default_factory=lambda: os.getenv("LLM_THINKING_LEVEL", "LOW")
    )

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
        self.fast_request_timeout_seconds = max(5, min(30, self.fast_request_timeout_seconds))
        self.max_provider_retries = max(0, min(1, self.max_provider_retries))
        self.retry_max_delay = max(0, min(2, self.retry_max_delay))


def get_router_settings() -> RouterSettings:
    """Return fresh settings instance from environment variables."""
    return RouterSettings()
