"""Fail-closed validation for generated Milestone 2 truth data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

import pandas as pd

from baltic_sales_intelligence.generation.reference_data import (
    ACCOUNT_STATUSES,
    CHANNELS,
    CITIES,
    COMPANY_SIZE_BANDS,
    COUNTRIES,
    CREDIT_TIERS,
    INDUSTRIES,
    PRIVATE_LABEL_NAMES,
    PUBLIC_COLUMNS,
    REGIONS,
    SEGMENTS,
)

if TYPE_CHECKING:
    from baltic_sales_intelligence.config import ProjectConfiguration


class GenerationValidationError(ValueError):
    """Raised when generated truth violates an approved contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GenerationValidationError(message)


def _missing(value: object) -> bool:
    return bool(pd.isna(value))


def _intervals_do_not_overlap(frame: pd.DataFrame, key: str) -> bool:
    for _, rows in frame.sort_values([key, "valid_from"]).groupby(key, sort=False):
        previous_end: date | None = None
        records = list(rows.itertuples(index=False))
        for index, row in enumerate(records):
            if not _missing(previous_end) and row.valid_from < previous_end:
                return False
            previous_end = row.valid_to
            if _missing(previous_end) and index != len(records) - 1:
                return False
    return True


def validate_reference_data(
    tables: dict[str, pd.DataFrame], configuration: ProjectConfiguration
) -> None:
    """Validate all reference tables as one relational, temporal contract."""

    missing = set(PUBLIC_COLUMNS).difference(tables)
    _require(not missing, f"missing required datasets: {sorted(missing)}")
    for name, columns in PUBLIC_COLUMNS.items():
        frame = tables[name]
        _require(tuple(frame.columns) == columns, f"{name}: output columns do not match contract")
        _require(not (set(frame.columns) & PRIVATE_LABEL_NAMES), f"{name}: private label leakage")
    optional_columns = {
        "suppliers": {"supplier_country_code"},
        "customers": {"credit_tier", "preferred_channel_code"},
        "customer_attribute_history": {"valid_to"},
        "salespeople": {"primary_region_id", "termination_date", "manager_salesperson_id"},
        "customer_account_assignments": {"valid_to"},
        "products": {"discontinued_date"},
        "product_supplier_relationships": {"valid_to"},
    }
    for name, columns in PUBLIC_COLUMNS.items():
        required = set(columns).difference(optional_columns.get(name, set()))
        _require(
            tables[name][sorted(required)].notna().all().all(),
            f"{name}: missing required field",
        )

    expected_counts = {
        "countries": 3,
        "regions": 13,
        "cities": 14,
        "customer_segments": 5,
        "industries": 11,
        "product_categories": 12,
        "product_subcategories": 12
        * configuration.generation.population.subcategories_per_category,
        "suppliers": configuration.generation.population.suppliers,
        "sales_channels": 5,
        "customers": configuration.generation.population.customers,
        "salespeople": configuration.generation.population.salespeople,
        "products": configuration.generation.population.products,
    }
    for name, count in expected_counts.items():
        _require(len(tables[name]) == count, f"{name}: expected {count} rows")

    primary_keys = {
        "countries": "country_code",
        "regions": "region_id",
        "cities": "city_id",
        "customer_segments": "segment_code",
        "industries": "industry_code",
        "product_categories": "category_id",
        "product_subcategories": "subcategory_id",
        "suppliers": "supplier_id",
        "sales_channels": "channel_code",
        "customers": "customer_id",
        "customer_attribute_history": "customer_version_id",
        "salespeople": "salesperson_id",
        "customer_account_assignments": "assignment_id",
        "products": "product_id",
        "product_supplier_relationships": "product_supplier_relationship_id",
        "date_dimension": "date_key",
    }
    for name, key in primary_keys.items():
        _require(tables[name][key].notna().all(), f"{name}: null primary key")
        _require(tables[name][key].is_unique, f"{name}: duplicate primary key")

    _require(
        tuple(tables["countries"].itertuples(index=False, name=None)) == COUNTRIES,
        "countries: frozen catalogue mismatch",
    )
    _require(
        tuple(tables["regions"].itertuples(index=False, name=None)) == REGIONS,
        "regions: frozen catalogue mismatch",
    )
    _require(
        tuple(tables["cities"].itertuples(index=False, name=None)) == CITIES,
        "cities: frozen catalogue mismatch",
    )

    regions = tables["regions"].set_index("region_id")
    for city in tables["cities"].itertuples(index=False):
        _require(
            regions.loc[city.region_id, "country_code"] == city.country_code,
            f"cities: invalid parent for {city.city_id}",
        )

    _require(
        set(tables["customer_segments"]["segment_code"]) == {x[0] for x in SEGMENTS},
        "customer_segments: enum mismatch",
    )
    _require(
        set(tables["industries"]["industry_code"]) == {x[0] for x in INDUSTRIES},
        "industries: enum mismatch",
    )
    _require(
        set(tables["sales_channels"]["channel_code"]) == {x[0] for x in CHANNELS},
        "sales_channels: enum mismatch",
    )

    customers = tables["customers"]
    _require(customers["customer_name"].notna().all(), "customers: missing name")
    _require(
        set(customers["account_status"]).issubset(ACCOUNT_STATUSES),
        "customers: invalid account status",
    )
    _require(
        set(customers["credit_tier"].dropna()).issubset(CREDIT_TIERS),
        "customers: invalid credit tier",
    )
    _require(
        set(customers["industry_code"]).issubset(set(tables["industries"]["industry_code"])),
        "customers: invalid industry FK",
    )
    _require(
        set(customers["preferred_channel_code"].dropna()).issubset(
            set(tables["sales_channels"]["channel_code"])
        ),
        "customers: invalid preferred channel FK",
    )
    _require(
        customers["acquisition_date"].le(configuration.application.reporting.period_end).all(),
        "customers: acquisition after reporting window",
    )

    history = tables["customer_attribute_history"]
    _require(
        set(history["customer_id"]) == set(customers["customer_id"]),
        "customer history: missing or orphan customer",
    )
    _require(
        _intervals_do_not_overlap(history, "customer_id"), "customer history: overlapping intervals"
    )
    current_counts = history.groupby("customer_id")["is_current"].sum()
    _require((current_counts == 1).all(), "customer history: exactly one current row required")
    _require(
        (history["is_current"] == history["valid_to"].isna()).all(),
        "customer history: current flag/end mismatch",
    )
    customer_acquisition = customers.set_index("customer_id")["acquisition_date"]
    for customer_id, rows in history.groupby("customer_id", sort=False):
        ordered = rows.sort_values("valid_from")
        _require(
            ordered.iloc[0]["valid_from"] == customer_acquisition[customer_id],
            f"customer history: coverage starts late for {customer_id}",
        )
        previous = None
        for row in ordered.itertuples(index=False):
            _require(
                _missing(row.valid_to) or row.valid_from < row.valid_to,
                f"customer history: invalid interval for {row.customer_version_id}",
            )
            if previous is not None:
                _require(
                    previous == row.valid_from, f"customer history: coverage gap for {customer_id}"
                )
            previous = row.valid_to
    _require(
        set(history["company_size_band"]).issubset(COMPANY_SIZE_BANDS),
        "customer history: invalid size band",
    )
    _require(
        set(history["segment_code"]).issubset(set(tables["customer_segments"]["segment_code"])),
        "customer history: invalid segment FK",
    )
    city_lookup = tables["cities"].set_index("city_id")
    for row in history.itertuples(index=False):
        city = city_lookup.loc[row.home_city_id]
        _require(
            city["region_id"] == row.home_region_id
            and city["country_code"] == row.home_country_code,
            f"customer history: inconsistent geography for {row.customer_version_id}",
        )

    salespeople = tables["salespeople"]
    salesperson_ids = set(salespeople["salesperson_id"])
    _require(
        set(salespeople["primary_country_code"]).issubset(set(tables["countries"]["country_code"])),
        "salespeople: invalid country FK",
    )
    _require(
        set(salespeople["manager_salesperson_id"].dropna()).issubset(salesperson_ids),
        "salespeople: invalid manager FK",
    )
    region_country = tables["regions"].set_index("region_id")["country_code"]
    _require(
        set(salespeople["primary_region_id"].dropna()).issubset(set(region_country.index)),
        "salespeople: invalid primary region FK",
    )
    for row in salespeople.itertuples(index=False):
        _require(
            _missing(row.termination_date) or row.hire_date < row.termination_date,
            f"salespeople: invalid employment interval for {row.salesperson_id}",
        )
        _require(
            _missing(row.primary_region_id)
            or region_country[row.primary_region_id] == row.primary_country_code,
            f"salespeople: invalid primary region for {row.salesperson_id}",
        )
        _require(
            _missing(row.manager_salesperson_id)
            or row.manager_salesperson_id != row.salesperson_id,
            f"salespeople: self-manager for {row.salesperson_id}",
        )

    assignments = tables["customer_account_assignments"]
    _require(
        set(assignments["customer_id"]).issubset(set(customers["customer_id"])),
        "assignments: invalid customer FK",
    )
    _require(
        set(assignments["salesperson_id"]).issubset(salesperson_ids),
        "assignments: invalid salesperson FK",
    )
    _require(
        _intervals_do_not_overlap(assignments, "customer_id"), "assignments: overlapping ownership"
    )
    people = salespeople.set_index("salesperson_id")
    for row in assignments.itertuples(index=False):
        person = people.loc[row.salesperson_id]
        _require(
            row.valid_from >= customer_acquisition[row.customer_id],
            f"assignments: pre-acquisition owner for {row.customer_id}",
        )
        _require(
            row.valid_from >= person["hire_date"],
            f"assignments: pre-employment owner for {row.assignment_id}",
        )
        _require(
            _missing(row.valid_to) or row.valid_from < row.valid_to,
            f"assignments: invalid interval for {row.assignment_id}",
        )
        if not _missing(person["termination_date"]):
            _require(
                not _missing(row.valid_to) and row.valid_to <= person["termination_date"],
                f"assignments: ownership exceeds employment for {row.assignment_id}",
            )

    subcategories = tables["product_subcategories"]
    _require(
        set(subcategories["category_id"]).issubset(
            set(tables["product_categories"]["category_id"])
        ),
        "product_subcategories: invalid category FK",
    )
    products = tables["products"]
    _require(products["sku"].is_unique, "products: duplicate SKU")
    _require(
        set(products["subcategory_id"]).issubset(set(subcategories["subcategory_id"])),
        "products: invalid subcategory FK",
    )
    for row in products.itertuples(index=False):
        _require(
            _missing(row.discontinued_date) or row.launch_date < row.discontinued_date,
            f"products: invalid lifecycle for {row.product_id}",
        )
        _require(
            (row.lifecycle_status == "active") == _missing(row.discontinued_date),
            f"products: lifecycle status mismatch for {row.product_id}",
        )
        for value in (row.reference_list_unit_price_eur, row.reference_standard_unit_cost_eur):
            _require(
                isinstance(value, Decimal) and value > 0 and value.as_tuple().exponent == -4,
                f"products: invalid monetary precision for {row.product_id}",
            )

    relationships = tables["product_supplier_relationships"]
    supplier_ids = set(tables["suppliers"]["supplier_id"])
    _require(
        set(relationships["product_id"]).issubset(set(products["product_id"])),
        "supplier relationships: invalid product FK",
    )
    _require(
        set(relationships["supplier_id"]).issubset(supplier_ids),
        "supplier relationships: invalid supplier FK",
    )
    _require(
        set(relationships["relationship_type"]).issubset({"primary", "alternative"}),
        "supplier relationships: invalid relationship type",
    )
    for product in products.itertuples(index=False):
        rows = relationships[relationships["product_id"] == product.product_id]
        _require(
            set(rows["supplier_id"]).issubset(supplier_ids),
            f"supplier relationships: orphan supplier for {product.product_id}",
        )
        for _, supplier_rows in rows.groupby("supplier_id", sort=False):
            ordered = supplier_rows.sort_values("valid_from")
            ends = ordered["valid_to"].tolist()
            starts = ordered["valid_from"].tolist()
            _require(
                all(not _missing(end) and end <= starts[i + 1] for i, end in enumerate(ends[:-1])),
                f"supplier relationships: overlap for {product.product_id}",
            )
        _require(
            all(
                _missing(row.valid_to) or row.valid_from < row.valid_to
                for row in rows.itertuples(index=False)
            ),
            f"supplier relationships: invalid interval for {product.product_id}",
        )
        primaries = rows[rows["relationship_type"] == "primary"].sort_values("valid_from")
        _require(
            len(primaries) >= 1 and primaries.iloc[0]["valid_from"] == product.launch_date,
            f"supplier relationships: primary coverage start for {product.product_id}",
        )
        for first, second in pairwise(primaries.itertuples(index=False)):
            _require(
                first.valid_to == second.valid_from,
                f"supplier relationships: primary gap for {product.product_id}",
            )
        expected_end = product.discontinued_date
        _require(
            primaries.iloc[-1]["valid_to"] == expected_end,
            f"supplier relationships: primary coverage end for {product.product_id}",
        )
    alternative_share = relationships.loc[
        relationships["relationship_type"] == "alternative", "product_id"
    ].nunique() / len(products)
    _require(
        abs(alternative_share - configuration.generation.catalogue.alternative_supplier_share)
        <= configuration.generation.catalogue.alternative_supplier_tolerance,
        "supplier relationships: alternative share outside tolerance",
    )

    dates = tables["date_dimension"]
    reporting = configuration.application.reporting
    expected_days = (reporting.extension_end - reporting.period_start).days + 1
    _require(len(dates) == expected_days, "date_dimension: incomplete coverage")
    _require(dates["full_date"].is_unique, "date_dimension: duplicate date")
    _require(
        dates.iloc[0]["full_date"] == reporting.period_start
        and dates.iloc[-1]["full_date"] == reporting.extension_end,
        "date_dimension: wrong boundaries",
    )
