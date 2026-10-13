"""DuckDB warehouse to governed Gold mart publication."""

from baltic_sales_intelligence.gold.builder import GoldBuildResult, GoldError, build_gold

__all__ = ["GoldBuildResult", "GoldError", "build_gold"]
