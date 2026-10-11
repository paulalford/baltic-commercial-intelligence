"""Deterministic raw-data defect injection."""

from baltic_sales_intelligence.quality.injection import (
    InjectionResult,
    inject_defects,
    load_validated_truth,
    publish_raw_injection,
)

__all__ = [
    "InjectionResult",
    "inject_defects",
    "load_validated_truth",
    "publish_raw_injection",
]
