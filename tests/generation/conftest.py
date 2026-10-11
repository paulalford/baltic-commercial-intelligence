"""Small deterministic configuration fixtures for generator tests."""

from pathlib import Path

import pytest

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.generation import generate_reference_tables

CONFIG_DIRECTORY = Path(__file__).parents[2] / "config"


@pytest.fixture(scope="session")
def small_configuration() -> ProjectConfiguration:
    baseline = load_configuration(CONFIG_DIRECTORY)
    payload = baseline.model_dump()
    payload["generation"]["population"].update(
        {
            "customers": 120,
            "products": 72,
            "salespeople": 12,
            "suppliers": 16,
            "orders_target": 500,
            "order_lines_min": 500,
            "order_lines_max": 3000,
            "return_events_min": 30,
            "return_events_max": 60,
        }
    )
    return ProjectConfiguration.model_validate(payload)


@pytest.fixture(scope="session")
def small_tables(small_configuration: ProjectConfiguration):
    return generate_reference_tables(small_configuration)
