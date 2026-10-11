"""Focused relational, cutoff, target and leakage checks for generated transactions."""

from decimal import Decimal

from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES, PUBLIC_COLUMNS


def test_transaction_schemas_and_configured_counts(small_configuration, small_tables) -> None:
    for name in ("orders", "order_lines", "returns", "sales_targets"):
        assert tuple(small_tables[name].columns) == PUBLIC_COLUMNS[name]
        assert not (set(small_tables[name].columns) & PRIVATE_LABEL_NAMES)
    assert len(small_tables["orders"]) == 500
    assert 500 <= len(small_tables["order_lines"]) <= 3000
    assert 30 <= len(small_tables["returns"]) <= 60


def test_nonfulfilled_lines_have_zero_recognised_postings(small_tables) -> None:
    nonfulfilled = set(
        small_tables["orders"].loc[
            small_tables["orders"]["order_status"] != "fulfilled", "order_id"
        ]
    )
    lines = small_tables["order_lines"]
    lines = lines[lines["order_id"].isin(nonfulfilled)]
    posted = [column for column in lines if column.startswith("posted_")]
    assert not lines.empty
    assert all(value == Decimal("0.00") for value in lines[posted].to_numpy().flat)


def test_return_caps_zero_posting_and_post_cutoff_events(small_configuration, small_tables) -> None:
    returns = small_tables["returns"]
    sold = small_tables["order_lines"].set_index("order_line_id")["quantity_ordered"]
    accepted = returns[returns["return_status"] == "accepted"]
    accepted_totals = accepted.groupby("order_line_id")["accepted_quantity"].sum()
    assert (accepted_totals <= sold.reindex(accepted_totals.index)).all()
    nonaccepted = returns[returns["return_status"] != "accepted"]
    assert (
        (
            nonaccepted[
                ["posted_refund_ex_vat_eur", "posted_recovered_cogs_eur", "posted_vat_reversal_eur"]
            ]
            == Decimal("0.00")
        )
        .all()
        .all()
    )
    cutoff = small_configuration.application.reporting.default_as_of_date
    assert (returns["return_decision_date"].dropna() > cutoff).any()


def test_target_leaf_grain_reconciles_without_hierarchy_rows(small_tables) -> None:
    targets = small_tables["sales_targets"]
    assert set(targets["allocation_type"]) == {"salesperson", "unallocated_country"}
    assert (
        not targets.duplicated(
            ["target_month", "metric_code", "salesperson_id"],
        )
        .loc[targets["salesperson_id"].notna()]
        .any()
    )
    grouped = targets.groupby(["target_month", "metric_code"])["target_value"].sum()
    by_country = (
        targets.groupby(["target_month", "metric_code", "country_code"])["target_value"]
        .sum()
        .groupby(level=[0, 1])
        .sum()
    )
    assert grouped.equals(by_country)


def test_nullable_sales_credit_is_preserved(small_tables) -> None:
    orders = small_tables["orders"]
    assert orders["credited_salesperson_id"].isna().any()
    assert orders.loc[orders["credited_salesperson_id"].isna(), "customer_id"].notna().all()
