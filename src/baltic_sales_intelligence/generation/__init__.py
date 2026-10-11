"""Deterministic synthetic reference-data generation."""

from typing import TYPE_CHECKING, Any

from baltic_sales_intelligence.generation.generator import (
    GenerationResult,
    build_distribution_summary,
    canonical_content_hash,
    generate_reference_data,
    generate_reference_tables,
)
from baltic_sales_intelligence.generation.validation import (
    GenerationValidationError,
    validate_reference_data,
)

if TYPE_CHECKING:
    from baltic_sales_intelligence.generation.scenario_validation import (
        publish_scenario_qa,
        validate_scenarios,
    )


def __getattr__(name: str) -> Any:
    """Lazily expose scenario QA helpers without preloading executable modules."""

    if name in {"publish_scenario_qa", "validate_scenarios"}:
        from baltic_sales_intelligence.generation import scenario_validation

        return getattr(scenario_validation, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GenerationResult",
    "GenerationValidationError",
    "build_distribution_summary",
    "canonical_content_hash",
    "generate_reference_data",
    "generate_reference_tables",
    "publish_scenario_qa",
    "validate_reference_data",
    "validate_scenarios",
]
