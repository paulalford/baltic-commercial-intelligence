"""Miniature publication integrity and read-only financial checks."""

# ruff: noqa: E501  # Small DuckDB DDL fixtures stay legible on one line.

from __future__ import annotations

import copy
import hashlib
from datetime import date
from pathlib import Path

import duckdb
import pytest

from baltic_sales_intelligence.config import load_configuration
from baltic_sales_intelligence.opportunities import builder
from baltic_sales_intelligence.opportunities.policy import load_policy
from tests.insights.test_core import AS_OF, event, insight_facts


def test_financial_reconciliation_and_read_only_sources(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.duckdb"
    forecast_path = tmp_path / "forecast.duckdb"
    warehouse_path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(warehouse_path)) as c:
        c.execute(
            "CREATE TABLE fact_financial_posting_historical(posting_date DATE,net_revenue_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2))"
        )
        c.execute("INSERT INTO fact_financial_posting_historical VALUES ('2026-09-15',10,3)")
    with duckdb.connect(str(gold_path)) as c:
        c.execute(
            "CREATE TABLE mart_commercial_month(reporting_month DATE,net_revenue_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2))"
        )
        c.execute("INSERT INTO mart_commercial_month VALUES ('2026-09-01',10,3)")
    with duckdb.connect(str(forecast_path)) as c:
        c.execute(
            "CREATE TABLE sales_monthly(reporting_month DATE,scope_code VARCHAR,net_revenue_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2))"
        )
        c.execute(
            "INSERT INTO sales_monthly VALUES ('2026-09-01','COMPANY',10,3),('2026-09-01','LT',10,3)"
        )
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (gold_path, forecast_path, warehouse_path)
    }
    with duckdb.connect(":memory:") as c:
        c.execute(f"ATTACH '{gold_path}' AS gold (READ_ONLY)")
        c.execute(f"ATTACH '{forecast_path}' AS forecasting (READ_ONLY)")
        c.execute(f"ATTACH '{warehouse_path}' AS warehouse (READ_ONLY)")
        builder._validate_finance(c, AS_OF)
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (gold_path, forecast_path, warehouse_path)
    }
    assert before == after
    with duckdb.connect(str(forecast_path)) as c:
        c.execute("UPDATE sales_monthly SET net_gross_profit_eur=4 WHERE scope_code='COMPANY'")
    with duckdb.connect(":memory:") as c:
        c.execute(f"ATTACH '{gold_path}' AS gold (READ_ONLY)")
        c.execute(f"ATTACH '{forecast_path}' AS forecasting (READ_ONLY)")
        c.execute(f"ATTACH '{warehouse_path}' AS warehouse (READ_ONLY)")
        with pytest.raises(builder.OpportunityContractError, match="differ"):
            builder._validate_finance(c, AS_OF)


def test_stage_dry_run_manifest_and_failed_replace_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_db = tmp_path / "source.duckdb"
    with duckdb.connect(str(source_db)) as c:
        c.execute("CREATE TABLE dummy(x INTEGER)")
    source_sha = hashlib.sha256(source_db.read_bytes()).hexdigest()
    sources = {
        "warehouse": (
            {
                "source_batch_id": "mini",
                "source_silver_manifest_sha256": "mini-silver",
                "database": {"sha256": source_sha},
            },
            "mini-manifest",
            source_db,
        )
    }
    monkeypatch.setattr(builder, "validate_sources", lambda configuration, root: sources)
    monkeypatch.setattr(builder, "_validate_finance", lambda con, cutoff: None)
    monkeypatch.setattr(builder, "load_facts", lambda con, cutoff: insight_facts())
    configuration = load_configuration(Path("config"))
    policy = load_policy(Path("config"))
    output = tmp_path / "out"
    dry = builder.build_opportunity_insights(configuration, policy, tmp_path, output, publish=False)
    assert dry.output_directory is None and not output.exists()
    result = builder.build_opportunity_insights(configuration, policy, tmp_path, output)
    assert (
        result.manifest["database"]["sha256"]
        == hashlib.sha256((output / builder.DATABASE_NAME).read_bytes()).hexdigest()
    )
    assert result.manifest["source_batch_id"] == "mini"
    assert result.manifest["opportunity_policy_hash"] == policy.deterministic_hash()
    with duckdb.connect(str(result.database_path), read_only=True) as c:
        assert c.execute("SELECT count(*) FROM opportunity").fetchone()[0] > 0
        assert (
            c.execute("SELECT count(DISTINCT rule_id) FROM management_insight").fetchone()[0] == 7
        )
    replay = builder.build_opportunity_insights(
        configuration, policy, tmp_path, tmp_path / "replay"
    )
    assert {name: obj["content_sha256"] for name, obj in result.manifest["objects"].items()} == {
        name: obj["content_sha256"] for name, obj in replay.manifest["objects"].items()
    }
    for table, ordering in (
        ("opportunity", "opportunity_id"),
        ("management_insight", "insight_id"),
        ("opportunity_overlap", "opportunity_id_a,opportunity_id_b,overlap_id"),
    ):
        with (
            duckdb.connect(str(result.database_path), read_only=True) as a,
            duckdb.connect(str(replay.database_path), read_only=True) as b,
        ):
            assert (
                a.execute(f"SELECT * FROM {table} ORDER BY {ordering}").fetchall()
                == b.execute(f"SELECT * FROM {table} ORDER BY {ordering}").fetchall()
            )
    before = hashlib.sha256((output / builder.DATABASE_NAME).read_bytes()).hexdigest()
    import baltic_sales_intelligence.customer.builder as customer_builder

    real_replace = customer_builder.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated swap failure")
        real_replace(source, target)

    monkeypatch.setattr(customer_builder.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated swap failure"):
        builder.build_opportunity_insights(configuration, policy, tmp_path, output, force=True)
    assert hashlib.sha256((output / builder.DATABASE_NAME).read_bytes()).hexdigest() == before


def test_future_facts_do_not_change_rule_or_return_opportunity() -> None:
    from baltic_sales_intelligence.insights.core import evaluate_insights
    from baltic_sales_intelligence.opportunities.core import build_opportunities

    facts = insight_facts()
    expected_rules = evaluate_insights(facts, load_policy("config"), AS_OF)
    expected_opps = build_opportunities(facts, load_policy("config"), AS_OF)[0]
    future = copy.deepcopy(facts)
    future["monthly"].append(
        dict(
            reporting_month=date(2026, 10, 1),
            scope="LV",
            revenue=999999,
            gp=-999999,
            posting_count=999,
        )
    )
    future["returns"].append(event("P1", date(2026, 10, 1), True, idx=999))
    future["private_planted_story"] = "synthetic-control-only"
    future["customers"][0]["private_planted_story"] = "reactivation"
    future["returns"][0]["private_planted_story"] = "quality"
    assert evaluate_insights(future, load_policy("config"), AS_OF) == expected_rules
    assert build_opportunities(future, load_policy("config"), AS_OF)[0] == expected_opps


def test_cli_insights_dry_run_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from baltic_sales_intelligence.pipeline import cli

    calls = []

    def fake_build(configuration, policy, source, output, *, publish, force):
        calls.append((policy.method_version, source, output, publish, force))
        return builder.OpportunityBuildResult(
            manifest={"source_batch_id": "mini", "objects": {"opportunity": {"row_count": 2}}},
            validations={"output": {"status": "passed"}},
        )

    monkeypatch.setattr(cli, "build_opportunity_insights", fake_build)
    code = cli.main(
        [
            "--config",
            "config",
            "--stage",
            "insights",
            "--dry-run",
            "--input",
            str(tmp_path),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code == cli.ExitCode.OK
    assert calls == [("opportunity-insight-v1", tmp_path, tmp_path / "out", False, False)]
