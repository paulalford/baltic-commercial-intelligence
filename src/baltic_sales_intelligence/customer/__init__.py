"""Governed customer-analytics publication."""

from baltic_sales_intelligence.customer.builder import (
    CustomerAnalyticsBuildResult,
    CustomerAnalyticsError,
    build_customer_analytics,
)

__all__ = [
    "CustomerAnalyticsBuildResult",
    "CustomerAnalyticsError",
    "build_customer_analytics",
]
