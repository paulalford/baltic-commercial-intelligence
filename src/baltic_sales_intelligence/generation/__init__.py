"""Deterministic synthetic reference-data generation."""

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

__all__ = [
    "GenerationResult",
    "GenerationValidationError",
    "build_distribution_summary",
    "canonical_content_hash",
    "generate_reference_data",
    "generate_reference_tables",
    "validate_reference_data",
]
