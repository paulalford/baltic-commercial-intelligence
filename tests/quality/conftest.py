"""Miniature published raw-batch fixtures for Milestone 6 tests."""

from pathlib import Path

import pytest

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.generation import generate_reference_data
from baltic_sales_intelligence.quality import publish_raw_injection

CONFIG_DIRECTORY = Path(__file__).parents[2] / "config"


@pytest.fixture(scope="session")
def quality_configuration() -> ProjectConfiguration:
    payload = load_configuration(CONFIG_DIRECTORY).model_dump()
    payload["generation"]["population"].update(
        {
            "customers": 60,
            "products": 36,
            "salespeople": 9,
            "suppliers": 10,
            "orders_target": 200,
            "order_lines_min": 200,
            "order_lines_max": 1200,
            "return_events_min": 20,
            "return_events_max": 30,
        }
    )
    payload["data_quality"]["injection"]["test_mode_overrides"] = {
        f"DQ{number:02d}": 2 for number in range(1, 15)
    }
    return ProjectConfiguration.model_validate(payload)


@pytest.fixture(scope="session")
def raw_batch(tmp_path_factory, quality_configuration: ProjectConfiguration) -> Path:
    root = tmp_path_factory.mktemp("quality-batch")
    truth = root / "truth"
    raw = root / "raw"
    generate_reference_data(quality_configuration, truth)
    publish_raw_injection(quality_configuration, truth, raw, root / "private-qa")
    # Production validation must not require the QA oracle to exist.
    private_registry = root / "private-qa" / "private_injection_registry.csv"
    private_registry.unlink()
    return raw
