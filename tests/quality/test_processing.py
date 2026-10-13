"""Focused behavioural tests for the deterministic data-quality pipeline."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from shutil import copytree

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import baltic_sales_intelligence.quality.processing as quality_processing
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.financial import allocate_freight
from baltic_sales_intelligence.quality import (
    DataQualityError,
    accepted_return_cap_violations,
    evaluate_private_registry,
    process_raw_batch,
    publish_silver,
)


@pytest.fixture(scope="module")
def quality_result(quality_configuration: ProjectConfiguration, raw_batch: Path):
    return process_raw_batch(quality_configuration, raw_batch)


def test_all_dq_rules_are_detected_without_private_registry(quality_result) -> None:
    assert set(quality_result.decisions["rule_id"]) == {
        f"DQ{number:02d}" for number in range(1, 15)
    }
    actions = quality_result.decisions.groupby("rule_id")["action"].agg(set).to_dict()
    assert actions["DQ01"] == {"discard_exact_duplicate"}
    assert actions["DQ02"] == {"quarantine"}
    assert actions["DQ03"] == {"repair"}
    assert actions["DQ04"] == {"repair"}
    assert actions["DQ05"] == {"repair"}
    assert actions["DQ09"] >= {"repair", "quarantine"}
    assert actions["DQ14"] >= {"discard_exact_duplicate", "quarantine"}
    assert all(
        "injection" not in column and "defect_id" not in column
        for frame in quality_result.silver.values()
        for column in frame.columns
    )


def test_source_rows_have_one_conserved_final_disposition(quality_result) -> None:
    for row in quality_result.dispositions.itertuples(index=False):
        assert row.received == (
            row.accepted_unchanged + row.repaired + row.quarantined + row.discarded_exact_duplicate
        )
        assert row.conservation_passed


def test_multiple_violations_share_one_final_disposition(
    tmp_path: Path,
    quality_configuration: ProjectConfiguration,
    raw_batch: Path,
) -> None:
    raw = tmp_path / "raw"
    copytree(raw_batch, raw)
    lines_path = raw / "order_lines.csv"
    lines = pd.read_csv(lines_path, dtype=str, keep_default_na=False)
    lines.loc[0, "quantity_ordered"] = "0"
    lines.loc[0, "discount_rate"] = "1.500000"
    lines.to_csv(lines_path, index=False, lineterminator="\n")
    manifest_path = raw / "raw_injection_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["datasets"]["order_lines"]["sha256"] = hashlib.sha256(
        lines_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = process_raw_batch(quality_configuration, raw)
    decisions = result.decisions.query("dataset == 'order_lines' and source_row == 2")
    assert set(decisions["rule_id"]) >= {"DQ07", "DQ08"}
    disposition = result.dispositions.query("scope_id == 'order_lines'").iloc[0]
    assert disposition.quarantined >= 1


def test_independent_violations_are_audited_after_initial_quarantine(
    tmp_path: Path,
    quality_configuration: ProjectConfiguration,
    raw_batch: Path,
    quality_result,
) -> None:
    raw = tmp_path / "raw"
    copytree(raw_batch, raw)
    lines_path = raw / "order_lines.csv"
    raw_lines = pd.read_csv(lines_path, dtype=str, keep_default_na=False)

    baseline_lines = quality_result.silver["order_lines"]
    order_counts = baseline_lines["order_id"].value_counts()
    multi_line_orders = order_counts.loc[order_counts >= 2].index
    returned_line_ids = set(quality_result.silver["returns"]["order_line_id"])
    targets = baseline_lines.loc[
        baseline_lines["order_id"].isin(multi_line_orders)
        & ~baseline_lines["order_line_id"].isin(returned_line_ids)
    ].drop_duplicates("order_id")
    targets = targets.head(3)
    assert len(targets) == 3

    target_indexes = [
        int(raw_lines.index[raw_lines["order_line_id"] == line_id][0])
        for line_id in targets["order_line_id"]
    ]
    quantity_cost_index, discount_money_index, discount_product_index = target_indexes
    raw_lines.loc[quantity_cost_index, "quantity_ordered"] = "0"
    raw_lines.loc[quantity_cost_index, "unit_cost_eur"] = ""
    raw_lines.loc[discount_money_index, "discount_rate"] = "-0.100000"
    raw_lines.loc[discount_money_index, "posted_net_sales_eur"] = "malformed"
    raw_lines.loc[discount_product_index, "discount_rate"] = "1.100000"
    raw_lines.loc[discount_product_index, "product_id"] = "PRODUCT-ORPHAN"
    raw_lines.to_csv(lines_path, index=False, lineterminator="\n")

    manifest_path = raw / "raw_injection_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["datasets"]["order_lines"]["sha256"] = hashlib.sha256(
        lines_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = process_raw_batch(quality_configuration, raw)
    rerun = process_raw_batch(quality_configuration, raw)
    source_rows = [index + 2 for index in target_indexes]
    expected_rules = (
        {"DQ07", "DQ09"},
        {"DQ08", "DQ11"},
        {"DQ08", "DQ10"},
    )
    target_decisions = result.decisions.loc[
        (result.decisions["dataset"] == "order_lines")
        & result.decisions["source_row"].isin(source_rows)
    ]
    for source_row, rules in zip(source_rows, expected_rules, strict=True):
        audited = target_decisions.loc[target_decisions["source_row"] == source_row]
        assert set(audited["rule_id"]) >= rules
        assert not audited.duplicated(["dataset", "source_row", "rule_id"]).any()

    target_quarantine = result.quarantine["order_lines"].loc[
        result.quarantine["order_lines"]["source_row"].isin(source_rows)
    ]
    assert list(target_quarantine["source_row"]) == source_rows
    pd.testing.assert_frame_equal(result.decisions, rerun.decisions)

    removed_ids = set(targets["order_line_id"])
    expected_lines = baseline_lines.loc[
        ~baseline_lines["order_line_id"].isin(removed_ids)
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(result.silver["order_lines"], expected_lines)
    for dataset, baseline in quality_result.silver.items():
        if dataset != "order_lines":
            pd.testing.assert_frame_equal(result.silver[dataset], baseline)


def test_repairs_are_idempotent_and_outputs_are_deterministic(
    quality_configuration: ProjectConfiguration, raw_batch: Path, quality_result
) -> None:
    rerun = process_raw_batch(quality_configuration, raw_batch)
    assert quality_result.manifest == rerun.manifest
    assert quality_result.dispositions.equals(rerun.dispositions)
    assert quality_result.decisions.equals(rerun.decisions)
    repaired = quality_result.decisions.query("action == 'repair'")
    assert (repaired["original_hash"] != repaired["output_hash"]).all()


def test_fk_and_temporal_failures_propagate_with_context(quality_result) -> None:
    cascades = {
        name: frame
        for name, frame in quality_result.quarantine.items()
        if not frame.empty and frame["dependency_context"].str.len().gt(0).any()
    }
    assert {"orders", "order_lines"} <= set(cascades)
    assert not quality_result.decisions.query("rule_id == 'DQ13'").empty
    assert quality_result.manifest["validation_summary"]["cascade_quarantine_count"] > 0


def test_return_states_caps_and_post_cutoff_events_are_preserved(quality_result) -> None:
    returns = quality_result.silver["returns"]
    requested = returns.loc[returns["return_status"] == "requested"]
    rejected = returns.loc[returns["return_status"] == "rejected"]
    accepted = returns.loc[returns["return_status"] == "accepted"]
    assert requested["accepted_quantity"].isna().all()
    assert requested["return_decision_date"].isna().all()
    assert rejected["accepted_quantity"].isna().all()
    assert rejected["return_decision_date"].notna().all()
    assert (accepted["accepted_quantity"] > 0).all()
    assert (returns["return_decision_date"].dropna().map(str) > "2026-09-30").any()


def test_ordered_cumulative_return_cap_ignores_nonaccepted_events() -> None:
    events = pd.DataFrame(
        [
            ("RET-REQ", "OL-1", None, "requested", None),
            ("RET-REJ", "OL-1", date(2025, 2, 1), "rejected", None),
            ("RET-001", "OL-1", date(2025, 2, 2), "accepted", 1),
            ("RET-002", "OL-1", date(2025, 2, 3), "accepted", 2),
            ("RET-003", "OL-1", date(2025, 2, 3), "accepted", 1),
        ],
        columns=(
            "return_id",
            "order_line_id",
            "return_decision_date",
            "return_status",
            "accepted_quantity",
        ),
    )
    assert accepted_return_cap_violations(events, {"OL-1": 3}) == [4]


def test_financial_child_lookup_preserves_order_allocations_and_rounding() -> None:
    all_lines = pd.DataFrame(
        [
            ("OL-A1", "ORD-A", Decimal("10.00"), 1, True),
            ("OL-B1", "ORD-B", Decimal("0.00"), 1, True),
            ("OL-A2", "ORD-A", Decimal("20.00"), 2, True),
            ("OL-A3", "ORD-A", Decimal("70.00"), 7, False),
            ("OL-B2", "ORD-B", Decimal("0.00"), 3, True),
        ],
        columns=(
            "order_line_id",
            "order_id",
            "posted_net_sales_eur",
            "quantity_ordered",
            "active",
        ),
    ).set_index("order_line_id")
    active_lines = all_lines.loc[all_lines["active"]]

    grouped = quality_processing._financial_children_by_order(active_lines)
    rerun = quality_processing._financial_children_by_order(active_lines)

    assert grouped == rerun
    assert grouped["ORD-A"] == ([Decimal("10.00"), Decimal("20.00")], [1, 2])
    assert grouped["ORD-B"] == ([Decimal("0.00"), Decimal("0.00")], [1, 3])
    assert "ORD-MISSING" not in grouped
    assert Decimal("70.00") not in grouped["ORD-A"][0]

    freight_by_order = {"ORD-A": Decimal("10.01"), "ORD-B": Decimal("0.05")}
    expected_allocations = {
        "ORD-A": [Decimal("3.34"), Decimal("6.67")],
        "ORD-B": [Decimal("0.01"), Decimal("0.04")],
    }
    for order_id, freight in freight_by_order.items():
        repeated_scan = active_lines.loc[active_lines["order_id"] == order_id]
        original = allocate_freight(
            freight,
            repeated_scan["posted_net_sales_eur"].tolist(),
            repeated_scan["quantity_ordered"].tolist(),
        )
        optimized = allocate_freight(freight, *grouped[order_id])
        assert optimized == original == expected_allocations[order_id]
        assert sum(optimized, Decimal("0.00")) == freight


def test_financial_lookup_preserves_exact_pipeline_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    quality_configuration: ProjectConfiguration,
    raw_batch: Path,
    quality_result,
) -> None:
    def repeated_scan_lookup(
        lines: pd.DataFrame,
    ) -> dict[object, tuple[list[Decimal], list[int]]]:
        children_by_order: dict[object, tuple[list[Decimal], list[int]]] = {}
        for order_id in lines["order_id"].drop_duplicates():
            children = lines.loc[lines["order_id"] == order_id]
            children_by_order[order_id] = (
                children["posted_net_sales_eur"].tolist(),
                children["quantity_ordered"].tolist(),
            )
        return children_by_order

    monkeypatch.setattr(
        quality_processing,
        "_financial_children_by_order",
        repeated_scan_lookup,
    )
    repeated_scan_result = process_raw_batch(quality_configuration, raw_batch)

    assert repeated_scan_result.manifest == quality_result.manifest
    pd.testing.assert_frame_equal(repeated_scan_result.decisions, quality_result.decisions)
    pd.testing.assert_frame_equal(repeated_scan_result.dispositions, quality_result.dispositions)
    for dataset in quality_result.silver:
        pd.testing.assert_frame_equal(
            repeated_scan_result.silver[dataset], quality_result.silver[dataset]
        )
        pd.testing.assert_frame_equal(
            repeated_scan_result.quarantine[dataset], quality_result.quarantine[dataset]
        )


def test_silver_finance_and_relational_gates_are_clean(quality_result) -> None:
    orders = quality_result.silver["orders"].set_index("order_id")
    lines = quality_result.silver["order_lines"]
    fulfilled = lines.loc[lines["order_id"].map(orders["order_status"]) == "fulfilled"].iloc[0]
    quantity = Decimal(fulfilled["quantity_ordered"])
    unrounded_list = quantity * fulfilled["list_unit_price_eur"]
    expected_list = unrounded_list.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    expected_discount = (unrounded_list * fulfilled["discount_rate"]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    expected_net = expected_list - expected_discount
    expected_cogs = (quantity * fulfilled["unit_cost_eur"]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    expected_vat = (expected_net * fulfilled["vat_rate_fraction"]).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    assert (
        fulfilled["posted_list_value_eur"],
        fulfilled["posted_discount_eur"],
        fulfilled["posted_net_sales_eur"],
        fulfilled["posted_cogs_eur"],
        fulfilled["posted_taxable_base_eur"],
        fulfilled["posted_vat_amount_eur"],
        fulfilled["posted_invoice_total_eur"],
    ) == (
        expected_list,
        expected_discount,
        expected_net,
        expected_cogs,
        expected_net,
        expected_vat,
        expected_net + expected_vat,
    )
    assert set(lines["order_id"]) <= set(orders.index)
    assert set(quality_result.silver["returns"]["order_line_id"]) <= set(lines["order_line_id"])
    targets = quality_result.silver["sales_targets"]
    salesperson = targets.loc[targets["allocation_type"] == "salesperson"]
    unallocated = targets.loc[targets["allocation_type"] == "unallocated_country"]
    assert not salesperson.duplicated(["target_month", "metric_code", "salesperson_id"]).any()
    assert not unallocated.duplicated(["target_month", "metric_code", "country_code"]).any()


def test_private_oracle_evaluation_is_a_post_publication_path(tmp_path: Path) -> None:
    registry = pd.DataFrame(
        [
            {
                "dataset": "orders",
                "defect_id": "DQ04",
                "raw_row_number": 2,
                "expected_downstream_action": "repair controlled code",
            }
        ]
    )
    decisions = pd.DataFrame(
        [
            {
                "dataset": "orders",
                "source_row": 2,
                "rule_id": "DQ04",
                "action": "repair",
            }
        ]
    )
    registry.to_csv(tmp_path / "registry.csv", index=False)
    decisions.to_parquet(tmp_path / "decisions.parquet", index=False)
    report = evaluate_private_registry(tmp_path / "registry.csv", tmp_path / "decisions.parquet")
    assert report["recall"] == 1.0
    assert report["action_accuracy"] == 1.0
    assert report["production_influence"].startswith("none")


def test_silver_publication_preserves_nullable_integer_and_decimal_types(
    tmp_path: Path,
    quality_configuration: ProjectConfiguration,
    raw_batch: Path,
) -> None:
    (tmp_path / "silver").mkdir()
    (tmp_path / "silver" / ".gitkeep").touch()
    (tmp_path / "quarantine").mkdir()
    (tmp_path / "quarantine" / ".gitkeep").touch()
    result = publish_silver(
        quality_configuration,
        raw_batch,
        tmp_path / "silver",
        tmp_path / "quarantine",
    )
    returns_schema = pq.read_schema(tmp_path / "silver" / "returns.parquet")
    lines_schema = pq.read_schema(tmp_path / "silver" / "order_lines.parquet")
    assert returns_schema.field("accepted_quantity") == pa.field(
        "accepted_quantity", pa.int64(), nullable=True
    )
    assert lines_schema.field("posted_net_sales_eur").type == pa.decimal128(18, 2)
    assert isinstance(result.silver["order_lines"].iloc[0]["posted_net_sales_eur"], Decimal)
    manifest = json.loads((tmp_path / "silver" / "silver_manifest.json").read_text())
    for metadata in manifest["datasets"].values():
        path = tmp_path / "silver" / metadata["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]


def test_tampered_batch_is_rejected_and_previous_publication_is_preserved(
    tmp_path: Path,
    quality_configuration: ProjectConfiguration,
    raw_batch: Path,
) -> None:
    silver = tmp_path / "silver"
    quarantine = tmp_path / "quarantine"
    publish_silver(quality_configuration, raw_batch, silver, quarantine)
    original_manifest = (silver / "silver_manifest.json").read_bytes()
    raw_manifest_path = raw_batch / "raw_injection_manifest.json"
    original_raw_manifest = raw_manifest_path.read_bytes()
    manifest = json.loads(original_raw_manifest)
    manifest["datasets"]["orders"]["sha256"] = "0" * 64
    raw_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        with pytest.raises(DataQualityError, match="checksum mismatch"):
            publish_silver(
                quality_configuration,
                raw_batch,
                silver,
                quarantine,
                force=True,
            )
        assert (silver / "silver_manifest.json").read_bytes() == original_manifest
    finally:
        raw_manifest_path.write_bytes(original_raw_manifest)
