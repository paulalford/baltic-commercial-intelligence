"""Package import and public-interface smoke tests."""

import importlib

import baltic_sales_intelligence


def test_top_level_package_exposes_version_and_configuration_loader() -> None:
    assert baltic_sales_intelligence.__version__ == "0.1.0"
    assert callable(baltic_sales_intelligence.load_configuration)


def test_all_declared_subpackages_import() -> None:
    names = (
        "generation",
        "quality",
        "ingestion",
        "transforms",
        "metrics",
        "customer",
        "product",
        "profitability",
        "statistics",
        "forecasting",
        "anomalies",
        "opportunities",
        "insights",
        "utils",
        "pipeline",
    )
    for name in names:
        module = importlib.import_module(f"baltic_sales_intelligence.{name}")
        assert module.__doc__
