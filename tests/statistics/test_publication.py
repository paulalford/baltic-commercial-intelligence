"""Miniature publication, input-tamper and rollback fixtures."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import duckdb
import pytest

from baltic_sales_intelligence.config import load_configuration
from baltic_sales_intelligence.customer import builder as customer_builder
from baltic_sales_intelligence.customer.builder import _publish_directory
from baltic_sales_intelligence.statistics.builder import _materialize
from baltic_sales_intelligence.statistics.contracts import validate_inputs
from test_support.warehouse_fixtures import CONFIG_DIRECTORY


def test_typed_catalogue_and_unapproved_maturity_have_no_estimate() -> None:
    con = duckdb.connect()
    results = {
        "STAT-01": {"status": "insufficient_evidence", "reason": "small_sample"},
        "STAT-02": {"status": "insufficient_evidence", "reason": "missing_market_month"},
        "STAT-03": {
            "status": "not_estimable",
            "reason": "mature_cohort_duration_not_approved",
        },
        "STAT-04": {"status": "insufficient_evidence", "reason": "sparse_segment"},
        "STAT-05": {"status": "insufficient_evidence", "reason": "too_few_customer_clusters"},
    }
    bridge = {
        "components": {"rounding_or_unallocated_residual": Decimal("0.01")},
        "observed_change_eur": Decimal("0.01"),
        "matched_sku_count": 0,
        "new_or_exited_sku_count": 0,
    }
    _materialize(con, results, bridge, date(2026, 9, 30))
    assert con.execute("SELECT count(*) FROM study_catalogue").fetchone()[0] == 5
    assert con.execute(
        "SELECT status,n_observations FROM study_catalogue WHERE study_id='STAT-03'"
    ).fetchone() == ("not_estimable", 0)
    assert con.execute(
        "SELECT estimate,ci_low,p_value FROM study_estimate WHERE estimate_id='mature_return_rate'"
    ).fetchone() == (None, None, None)
    assert con.execute(
        "SELECT amount_eur FROM estonia_gp_bridge "
        "WHERE component='rounding_or_unallocated_residual'"
    ).fetchone()[0] == Decimal("0.01")
    assert con.execute(
        "SELECT amount_eur FROM study_sensitivity WHERE check_name='bridge_reconciliation'"
    ).fetchone()[0] == Decimal("0.01")


def test_typed_segment_and_basket_estimates_are_queryable() -> None:
    con = duckdb.connect()
    results = {
        "STAT-01": {"status": "insufficient_evidence", "reason": "fixture"},
        "STAT-02": {"status": "insufficient_evidence", "reason": "fixture"},
        "STAT-03": {"status": "not_estimable", "reason": "mature_cohort_duration_not_approved"},
        "STAT-04": {
            "status": "association_only",
            "n": 100,
            "clusters": 50,
            "standardized_rates": {"A": 0.4, "B": 0.6},
            "contrasts": [
                {
                    "segment": "B",
                    "baseline": "A",
                    "estimate": 20.0,
                    "ci_low": 5.0,
                    "ci_high": 35.0,
                    "p_value": 0.01,
                    "q_value": 0.01,
                }
            ],
        },
        "STAT-05": {
            "status": "association_only",
            "basket_count": 80,
            "clusters": 40,
            "pairs_tested": 1,
            "rows": [
                {
                    "item_a": "A",
                    "item_b": "B",
                    "status": "association_only",
                    "basket_count": 80,
                    "item_a_baskets": 40,
                    "item_b_baskets": 40,
                    "joint_baskets": 30,
                    "pair_support": 0.375,
                    "confidence_a_to_b": 0.75,
                    "confidence_b_to_a": 0.75,
                    "expected_joint_baskets": 20.0,
                    "lift": 1.5,
                    "ci_low": 1.2,
                    "ci_high": 1.8,
                    "p_value": 0.01,
                    "q_value": 0.01,
                    "customer_clusters": 40,
                    "bootstrap_valid_replicates": 200,
                }
            ],
        },
    }
    _materialize(con, results, None, date(2026, 9, 30))
    assert con.execute(
        "SELECT estimate,ci_low,ci_high FROM study_estimate WHERE estimate_id='retention_B_vs_A'"
    ).fetchone() == (20.0, 5.0, 35.0)
    assert con.execute(
        "SELECT lift,expected_joint_baskets,q_value FROM basket_pair_result"
    ).fetchone() == (1.5, 20.0, 0.01)


def test_tampered_warehouse_checksum_fails_closed(tmp_path: Path) -> None:
    configuration = load_configuration(Path("config"))
    warehouse = tmp_path / "database"
    warehouse.mkdir()
    (warehouse / "baltic_sales.duckdb").write_bytes(b"tampered")
    (warehouse / "warehouse_manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "bsi.warehouse_publication",
                "schema_version": 1,
                "stage": "warehouse",
                "publication_status": "passed",
                "build_config_hash": configuration.deterministic_hash(),
                "source_batch_id": "8e33878d4c2719cc",
                "database": {"path": "baltic_sales.duckdb", "sha256": "0" * 64},
            }
        )
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_inputs(
            configuration,
            warehouse,
            tmp_path / "gold",
            tmp_path / "customer",
            tmp_path / "product",
        )


def test_atomic_swap_restores_previous_publication_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "statistics"
    output.mkdir()
    (output / "statistical_analysis_manifest.json").write_text("previous-valid")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "statistical_analysis_manifest.json").write_text("replacement")
    real_replace = customer_builder.os.replace

    def fail_second_swap(source: str | Path, destination: str | Path) -> None:
        if Path(source) == staged:
            raise OSError("simulated swap failure")
        real_replace(source, destination)

    monkeypatch.setattr(customer_builder.os, "replace", fail_second_swap)
    with pytest.raises(OSError, match="simulated swap failure"):
        _publish_directory(staged, output, force=True)
    assert (output / "statistical_analysis_manifest.json").read_text() == "previous-valid"
    assert (staged / "statistical_analysis_manifest.json").read_text() == "replacement"


def test_statistics_cli_dry_run_routes_four_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baltic_sales_intelligence.pipeline.cli import ExitCode, main

    copytree(CONFIG_DIRECTORY, tmp_path / "config")
    called = {}

    def fake_build(configuration, warehouse, gold, customer, product, output, *, publish, force):
        called.update(
            warehouse=warehouse,
            gold=gold,
            customer=customer,
            product=product,
            output=output,
            publish=publish,
            force=force,
        )
        return SimpleNamespace(
            manifest={
                "objects": {"study_catalogue": {"row_count": 5}},
                "source_batch_id": "8e33878d4c2719cc",
            }
        )

    monkeypatch.setattr(
        "baltic_sales_intelligence.pipeline.cli.build_statistical_analysis", fake_build
    )
    code = main(["--config", str(tmp_path / "config"), "--stage", "statistics", "--dry-run"])
    assert code == ExitCode.OK
    assert called == {
        "warehouse": tmp_path / "data/database",
        "gold": tmp_path / "data/gold",
        "customer": tmp_path / "data/customer",
        "product": tmp_path / "data/product",
        "output": tmp_path / "data/statistics",
        "publish": False,
        "force": False,
    }
