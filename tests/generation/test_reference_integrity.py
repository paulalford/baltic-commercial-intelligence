"""Focused reference, temporal, geography and leakage invariants."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from baltic_sales_intelligence.generation.generator import build_manifest
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.generation.validation import (
    GenerationValidationError,
    validate_reference_data,
)


def test_exact_configured_counts_and_referential_integrity(
    small_configuration,
    small_tables,
) -> None:
    assert len(small_tables["countries"]) == 3
    assert len(small_tables["regions"]) == 13
    assert len(small_tables["cities"]) == 14
    assert len(small_tables["customers"]) == 120
    assert len(small_tables["products"]) == 72
    assert len(small_tables["salespeople"]) == 12
    assert len(small_tables["product_categories"]) == 12
    assert len(small_tables["product_subcategories"]) == 48
    validate_reference_data(small_tables, small_configuration)


def test_frozen_geographic_crosswalk(small_tables) -> None:
    cities = small_tables["cities"].set_index("city_id")
    assert cities.loc["EE-NARVA", "region_id"] == "EE-45"
    assert cities.loc["LV-LIEPAJA", "region_id"] == "LV-KU"
    assert cities.loc["LT-VILNIUS", "region_id"] == "LT-VL"


def test_customer_history_is_contiguous_and_half_open(small_tables) -> None:
    history = small_tables["customer_attribute_history"]
    changed = history.groupby("customer_id").filter(lambda rows: len(rows) > 1)
    assert not changed.empty
    for _, rows in history.sort_values("valid_from").groupby("customer_id"):
        records = list(rows.itertuples(index=False))
        assert sum(row.is_current for row in records) == 1
        assert records[-1].valid_to is None
        for previous, current in pairwise(records):
            assert previous.valid_to == current.valid_from
            boundary = current.valid_from
            assert previous.valid_from <= boundary
            assert not (previous.valid_from <= boundary < previous.valid_to)


def test_assignment_intervals_respect_acquisition_and_employment(small_tables) -> None:
    acquisition = small_tables["customers"].set_index("customer_id")["acquisition_date"]
    people = small_tables["salespeople"].set_index("salesperson_id")
    for assignment in small_tables["customer_account_assignments"].itertuples(index=False):
        person = people.loc[assignment.salesperson_id]
        assert assignment.valid_from >= acquisition[assignment.customer_id]
        assert assignment.valid_from >= person["hire_date"]
        if person["termination_date"] is not None:
            assert assignment.valid_to <= person["termination_date"]


def test_supplier_primary_coverage_and_alternative_share(
    small_configuration,
    small_tables,
) -> None:
    products = small_tables["products"].set_index("product_id")
    relationships = small_tables["product_supplier_relationships"]
    for product_id, rows in relationships.groupby("product_id"):
        primary = rows[rows["relationship_type"] == "primary"].sort_values("valid_from")
        assert primary.iloc[0]["valid_from"] == products.loc[product_id, "launch_date"]
        assert primary.iloc[-1]["valid_to"] == products.loc[product_id, "discontinued_date"]
        for left, right in zip(primary.itertuples(), primary.iloc[1:].itertuples(), strict=False):
            assert left.valid_to == right.valid_from
    actual = relationships.loc[
        relationships["relationship_type"] == "alternative", "product_id"
    ].nunique() / len(products)
    expected = small_configuration.generation.catalogue.alternative_supplier_share
    assert (
        abs(actual - expected)
        <= small_configuration.generation.catalogue.alternative_supplier_tolerance
    )


def test_calendar_handles_leap_day_iso_boundary_and_cutoff(small_tables) -> None:
    calendar = small_tables["date_dimension"].set_index("full_date")
    assert date(2024, 2, 29) in calendar.index
    assert calendar.loc[date(2022, 1, 1), "iso_week_year"] == 2021
    assert bool(calendar.loc[date(2026, 9, 30), "is_within_reporting_window"])
    assert not bool(calendar.loc[date(2026, 10, 1), "is_within_reporting_window"])


def test_money_has_fixed_four_decimal_precision(small_tables) -> None:
    for row in small_tables["products"].itertuples(index=False):
        assert isinstance(row.reference_list_unit_price_eur, Decimal)
        assert row.reference_list_unit_price_eur.as_tuple().exponent == -4
        assert row.reference_standard_unit_cost_eur.as_tuple().exponent == -4


def test_no_private_generator_labels_leak_to_tables_or_manifest(
    small_configuration,
    small_tables,
) -> None:
    assert all(not (set(table.columns) & PRIVATE_LABEL_NAMES) for table in small_tables.values())
    manifest_text = str(build_manifest(small_configuration, small_tables))
    assert all(label not in manifest_text for label in PRIVATE_LABEL_NAMES)


def test_invalid_geographic_parent_fails_explicitly(
    small_configuration,
    small_tables,
) -> None:
    corrupted = {name: table.copy() for name, table in small_tables.items()}
    corrupted["cities"].loc[corrupted["cities"]["city_id"] == "EE-NARVA", "region_id"] = "EE-37"
    with pytest.raises(GenerationValidationError, match="frozen catalogue mismatch"):
        validate_reference_data(corrupted, small_configuration)


def test_overlapping_customer_history_fails_explicitly(
    small_configuration,
    small_tables,
) -> None:
    corrupted = {name: table.copy() for name, table in small_tables.items()}
    history = corrupted["customer_attribute_history"]
    customer_id = history.groupby("customer_id").size().loc[lambda value: value > 1].index[0]
    indexes = history.index[history["customer_id"] == customer_id].tolist()
    history.loc[indexes[0], "valid_to"] = history.loc[indexes[1], "valid_from"] + timedelta(days=1)
    with pytest.raises(GenerationValidationError, match="overlapping intervals"):
        validate_reference_data(corrupted, small_configuration)
