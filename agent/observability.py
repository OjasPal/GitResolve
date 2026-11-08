import datetime
from dataclasses import asdict, dataclass
from typing import Any

from agent.config import DEFAULT_PRICING, PricingConfig


@dataclass
class UsageRecord:
    provider: str
    model_id: str
    task_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_cost_usd: float | None = None
    cost_is_estimate: bool = True
    usage_available: bool = True
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_cost(
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None = None,
    pricing_catalog: dict[str, PricingConfig] | None = None,
) -> float | None:
    """Calculate estimated inference cost in USD based on configurable pricing."""
    if input_tokens is None and output_tokens is None:
        return None

    catalog = pricing_catalog or DEFAULT_PRICING
    pricing = catalog.get(model_id)
    if not pricing:
        return None

    cost = 0.0
    if input_tokens is not None:
        # non-cached prompt tokens
        cached = cached_tokens or 0
        billable_input = max(0, input_tokens - cached)
        cost += (billable_input / 1_000_000.0) * pricing.prompt_per_million
        cost += (cached / 1_000_000.0) * pricing.cache_read_per_million

    if output_tokens is not None:
        cost += (output_tokens / 1_000_000.0) * pricing.completion_per_million

    return round(cost, 6)


def create_usage_record(
    provider: str,
    model_id: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cached_tokens: int | None = None,
    task_id: str | None = None,
    pricing_catalog: dict[str, PricingConfig] | None = None,
) -> UsageRecord:
    """Create a structured usage record with pricing calculation."""
    if input_tokens is None and output_tokens is None:
        return UsageRecord(
            provider=provider,
            model_id=model_id,
            task_id=task_id,
            usage_available=False,
            estimated_cost_usd=None,
        )

    estimated_cost = calculate_cost(
        model_id=model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        pricing_catalog=pricing_catalog,
    )

    return UsageRecord(
        provider=provider,
        model_id=model_id,
        task_id=task_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        estimated_cost_usd=estimated_cost,
        cost_is_estimate=True,
        usage_available=True,
    )


def sanitize_error(error: Exception | str) -> str:
    """Sanitize error messages to avoid leaking keys, credentials, or file paths."""
    from app.core.errors import public_error
    return public_error(error if isinstance(error, Exception) else RuntimeError(error)).message
