"""Explicit raw and Silver contracts for the Milestone 6 quality stage."""

from __future__ import annotations

from decimal import Decimal

import pandera.pandas as pa
import pyarrow as arrow

from baltic_sales_intelligence.generation.reference_data import (
    CHANNELS,
    INDUSTRIES,
    PUBLIC_COLUMNS,
    SEGMENTS,
)

PRIMARY_KEYS = {name: columns[0] for name, columns in PUBLIC_COLUMNS.items()}

OPTIONAL_COLUMNS = {
    "suppliers": {"supplier_country_code"},
    "customers": {"credit_tier", "preferred_channel_code"},
    "customer_attribute_history": {"valid_to"},
    "salespeople": {"primary_region_id", "termination_date", "manager_salesperson_id"},
    "customer_account_assignments": {"valid_to"},
    "products": {"discontinued_date"},
    "product_supplier_relationships": {"valid_to"},
    "orders": {
        "fulfilled_date",
        "cancelled_date",
        "credited_salesperson_id",
        "payment_terms_code",
    },
    "returns": {
        "return_decision_date",
        "accepted_quantity",
        "restocking_outcome_code",
        "refund_unit_ex_vat_eur",
    },
    "sales_targets": {"salesperson_id"},
}

DATE_COLUMNS = frozenset(
    {
        "acquisition_date",
        "valid_from",
        "valid_to",
        "hire_date",
        "termination_date",
        "launch_date",
        "discontinued_date",
        "full_date",
        "month_start_date",
        "order_date",
        "fulfilled_date",
        "cancelled_date",
        "return_requested_date",
        "return_decision_date",
        "target_month",
    }
)

INTEGER_COLUMNS = frozenset(
    {
        "date_key",
        "calendar_year",
        "calendar_quarter",
        "calendar_month",
        "iso_week",
        "iso_week_year",
        "iso_weekday",
        "day_of_month",
        "day_of_year",
        "line_number",
        "quantity_ordered",
        "requested_quantity",
        "accepted_quantity",
    }
)

BOOLEAN_COLUMNS = frozenset({"is_current", "is_weekend", "is_within_reporting_window"})

UNIT_DECIMAL_COLUMNS = frozenset(
    {
        "reference_list_unit_price_eur",
        "reference_standard_unit_cost_eur",
        "list_unit_price_eur",
        "realised_unit_price_eur",
        "unit_cost_eur",
        "refund_unit_ex_vat_eur",
    }
)
RATE_DECIMAL_COLUMNS = frozenset({"discount_rate", "vat_rate_fraction"})
MONEY_DECIMAL_COLUMNS = frozenset(
    {
        "outbound_freight_eur",
        "posted_list_value_eur",
        "posted_discount_eur",
        "posted_net_sales_eur",
        "posted_cogs_eur",
        "posted_taxable_base_eur",
        "posted_vat_amount_eur",
        "posted_invoice_total_eur",
        "posted_refund_ex_vat_eur",
        "posted_recovered_cogs_eur",
        "posted_vat_reversal_eur",
        "returns_freight_eur",
        "target_value",
    }
)
DECIMAL_COLUMNS = UNIT_DECIMAL_COLUMNS | RATE_DECIMAL_COLUMNS | MONEY_DECIMAL_COLUMNS

ENUMS: dict[str, frozenset[str]] = {
    "reporting_currency": frozenset({"EUR"}),
    "account_status": frozenset({"active", "suspended", "closed"}),
    "credit_tier": frozenset({"tier_1", "tier_2", "tier_3"}),
    "company_size_band": frozenset({"micro", "small", "medium", "large"}),
    "lifecycle_status": frozenset({"active", "discontinued"}),
    "relationship_type": frozenset({"primary", "alternative"}),
    "order_status": frozenset({"placed", "fulfilled", "cancelled"}),
    "payment_terms_code": frozenset({"prepaid", "net_14", "net_30", "net_45"}),
    "vat_treatment_code": frozenset({"sim_standard", "sim_zero"}),
    "return_status": frozenset({"requested", "accepted", "rejected"}),
    "return_reason_code": frozenset(
        {
            "defective",
            "wrong_item",
            "damaged_in_transit",
            "ordered_in_error",
            "quality_issue",
            "not_as_expected",
            "duplicate_order",
        }
    ),
    "restocking_outcome_code": frozenset({"restockable", "written_off"}),
    "allocation_type": frozenset({"salesperson", "unallocated_country"}),
    "metric_code": frozenset({"net_revenue_eur", "gross_profit_eur"}),
    "segment_code": frozenset(code for code, _name in SEGMENTS),
    "industry_code": frozenset(code for code, _name in INDUSTRIES),
    "channel_code": frozenset(code for code, _name in CHANNELS),
    "preferred_channel_code": frozenset(code for code, _name in CHANNELS),
}

FOREIGN_KEYS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "regions": (("country_code", "countries", "country_code"),),
    "cities": (
        ("region_id", "regions", "region_id"),
        ("country_code", "countries", "country_code"),
    ),
    "product_subcategories": (("category_id", "product_categories", "category_id"),),
    "customers": (
        ("industry_code", "industries", "industry_code"),
        ("preferred_channel_code", "sales_channels", "channel_code"),
    ),
    "customer_attribute_history": (
        ("customer_id", "customers", "customer_id"),
        ("segment_code", "customer_segments", "segment_code"),
        ("home_city_id", "cities", "city_id"),
        ("home_region_id", "regions", "region_id"),
        ("home_country_code", "countries", "country_code"),
    ),
    "salespeople": (
        ("primary_country_code", "countries", "country_code"),
        ("primary_region_id", "regions", "region_id"),
        ("manager_salesperson_id", "salespeople", "salesperson_id"),
    ),
    "customer_account_assignments": (
        ("customer_id", "customers", "customer_id"),
        ("salesperson_id", "salespeople", "salesperson_id"),
    ),
    "products": (("subcategory_id", "product_subcategories", "subcategory_id"),),
    "product_supplier_relationships": (
        ("product_id", "products", "product_id"),
        ("supplier_id", "suppliers", "supplier_id"),
    ),
    "orders": (
        ("customer_id", "customers", "customer_id"),
        ("channel_code", "sales_channels", "channel_code"),
        ("ship_to_city_id", "cities", "city_id"),
        ("credited_salesperson_id", "salespeople", "salesperson_id"),
    ),
    "order_lines": (
        ("order_id", "orders", "order_id"),
        ("product_id", "products", "product_id"),
    ),
    "returns": (("order_line_id", "order_lines", "order_line_id"),),
    "sales_targets": (
        ("salesperson_id", "salespeople", "salesperson_id"),
        ("country_code", "countries", "country_code"),
    ),
}


def raw_schema(dataset: str) -> pa.DataFrameSchema:
    """Return a strict Pandera transport schema without coercing corrupt payloads."""

    return pa.DataFrameSchema(
        {column: pa.Column(str, nullable=False) for column in PUBLIC_COLUMNS[dataset]},
        strict=True,
        ordered=True,
        coerce=False,
        name=f"bsi.raw.{dataset}.v1",
    )


def silver_pandera_schema(dataset: str) -> pa.DataFrameSchema:
    """Return row-level Pandera gates applied after relational validation."""

    optional = OPTIONAL_COLUMNS.get(dataset, set())
    primary_key = PRIMARY_KEYS[dataset]
    columns: dict[str, pa.Column] = {}
    for column in PUBLIC_COLUMNS[dataset]:
        checks = []
        if column in ENUMS:
            checks.append(pa.Check.isin(ENUMS[column]))
        if column in {"quantity_ordered", "requested_quantity"}:
            checks.append(pa.Check.greater_than(0))
        if column == "discount_rate":
            checks.append(
                pa.Check(lambda series: series.map(lambda value: Decimal("0") <= value <= 1))
            )
        columns[column] = pa.Column(
            None,
            checks=checks,
            nullable=column in optional,
            unique=column == primary_key,
        )
    return pa.DataFrameSchema(
        columns,
        strict=True,
        ordered=True,
        coerce=False,
        name=f"bsi.silver.{dataset}.v1",
    )


def arrow_schema(dataset: str) -> arrow.Schema:
    """Return the explicit typed Silver schema for a contracted dataset."""

    fields: list[arrow.Field] = []
    optional = OPTIONAL_COLUMNS.get(dataset, set())
    for column in PUBLIC_COLUMNS[dataset]:
        if column in DATE_COLUMNS:
            data_type = arrow.date32()
        elif column in INTEGER_COLUMNS:
            data_type = arrow.int64()
        elif column in BOOLEAN_COLUMNS:
            data_type = arrow.bool_()
        elif column in UNIT_DECIMAL_COLUMNS:
            data_type = arrow.decimal128(18, 4)
        elif column in RATE_DECIMAL_COLUMNS:
            data_type = arrow.decimal128(9, 6)
        elif column in MONEY_DECIMAL_COLUMNS:
            data_type = arrow.decimal128(18, 2)
        else:
            data_type = arrow.string()
        fields.append(arrow.field(column, data_type, nullable=column in optional))
    return arrow.schema(fields)


def decimal_quantum(column: str) -> Decimal:
    if column in RATE_DECIMAL_COLUMNS:
        return Decimal("0.000001")
    if column in UNIT_DECIMAL_COLUMNS:
        return Decimal("0.0001")
    return Decimal("0.01")
