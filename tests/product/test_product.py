# ruff: noqa: E501
"""Independent miniature financial, classification, basket, and return fixture."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from baltic_sales_intelligence.product.builder import _validate

SQL = Path(__file__).parents[2] / "sql/analysis/product_intelligence.sql"


@pytest.fixture
def miniature(tmp_path: Path):
    wh = tmp_path / "warehouse.duckdb"
    gold = tmp_path / "gold.duckdb"
    w = duckdb.connect(str(wh))
    w.execute(
        "CREATE TABLE dim_product(product_id VARCHAR,sku VARCHAR,product_name VARCHAR,subcategory_id VARCHAR,subcategory_name VARCHAR,category_id VARCHAR,category_name VARCHAR,launch_date DATE,discontinued_date DATE,lifecycle_status VARCHAR)"
    )
    w.executemany(
        "INSERT INTO dim_product VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("P1", "P1", "Stable", "S1", "S1", "C", "C", date(2022, 1, 1), None, "active"),
            ("P2", "P2", "Variable", "S2", "S2", "C", "C", date(2022, 1, 1), None, "active"),
            ("P3", "P3", "Loss", "S3", "S3", "C", "C", date(2022, 1, 1), None, "active"),
            ("P4", "P4", "New", "S4", "S4", "C", "C", date(2026, 8, 1), None, "active"),
            ("P5", "P5", "Tie", "S5", "S5", "C", "C", date(2022, 1, 1), None, "active"),
        ],
    )
    w.execute("CREATE TABLE dim_supplier(supplier_id VARCHAR,supplier_name VARCHAR)")
    w.execute(
        "CREATE TABLE dim_customer(customer_sk BIGINT,home_country_code VARCHAR,segment_code VARCHAR)"
    )
    w.execute("INSERT INTO dim_customer VALUES (1,'LT','CORE')")
    w.execute(
        "CREATE TABLE bridge_product_supplier(product_id VARCHAR,supplier_id VARCHAR,relationship_type VARCHAR,valid_from DATE,valid_to DATE)"
    )
    w.execute("CREATE TABLE dim_date(full_date DATE,month_start_date DATE)")
    months = [date(2025 + (9 + i) // 12, (9 + i) % 12 + 1, 1) for i in range(12)]
    w.executemany("INSERT INTO dim_date VALUES (?,?)", [(m, m) for m in months])
    w.execute(
        "CREATE TABLE fact_order_line(order_line_id VARCHAR,order_id VARCHAR,product_id VARCHAR,order_status VARCHAR,fulfilled_date DATE,quantity_ordered BIGINT)"
    )
    lines = [
        (f"L{i}", f"O{i}", "P1", "fulfilled", m, 3 if i == 0 else 1) for i, m in enumerate(months)
    ]
    lines += [
        ("L12", "O12", "P2", "fulfilled", date(2026, 9, 1), 12),
        ("L13", "O13", "P3", "cancelled", None, 1),
        ("L14", "O14", "P5", "fulfilled", date(2026, 9, 1), 1),
    ]
    lines += [
        ("B1", "B1", "P1", "fulfilled", date(2024, 1, 1), 1),
        ("B1a", "B1", "P1", "fulfilled", date(2024, 1, 1), 1),
        ("B1b", "B1", "P2", "fulfilled", date(2024, 1, 1), 1),
        ("B2", "B2", "P1", "fulfilled", date(2024, 1, 1), 1),
        ("B3", "B3", "P1", "fulfilled", date(2024, 1, 1), 1),
        ("B4", "B4", "P2", "fulfilled", date(2024, 1, 1), 1),
    ]
    w.executemany("INSERT INTO fact_order_line VALUES (?,?,?,?,?,?)", lines)
    w.execute(
        "CREATE TABLE fact_return_event(return_id VARCHAR,order_line_id VARCHAR,order_id VARCHAR,product_id VARCHAR,return_requested_date DATE,return_decision_date DATE,requested_quantity BIGINT,accepted_quantity BIGINT,return_status VARCHAR,return_reason_code VARCHAR,restocking_outcome_code VARCHAR,posted_refund_ex_vat_eur DECIMAL(18,2),posted_recovered_cogs_eur DECIMAL(18,2))"
    )
    w.executemany(
        "INSERT INTO fact_return_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "R1",
                "L0",
                "O0",
                "P1",
                date(2025, 10, 2),
                date(2025, 10, 3),
                1,
                1,
                "accepted",
                "defective",
                "written_off",
                Decimal("2"),
                Decimal("0"),
            ),
            (
                "R2",
                "L1",
                "O1",
                "P1",
                date(2025, 11, 2),
                date(2025, 11, 3),
                1,
                None,
                "rejected",
                "ordered_in_error",
                None,
                Decimal("0"),
                Decimal("0"),
            ),
            (
                "R4",
                "L0",
                "O0",
                "P1",
                date(2025, 10, 4),
                date(2025, 10, 5),
                1,
                1,
                "accepted",
                "not_as_expected",
                "written_off",
                Decimal("1"),
                Decimal("0"),
            ),
            (
                "R5",
                "L0",
                "O0",
                "P1",
                date(2026, 9, 29),
                date(2026, 10, 2),
                1,
                1,
                "accepted",
                "quality_issue",
                "written_off",
                Decimal("2"),
                Decimal("0"),
            ),
            (
                "R3",
                "L2",
                "O2",
                "P1",
                date(2026, 10, 2),
                date(2026, 10, 3),
                1,
                1,
                "accepted",
                "quality_issue",
                "written_off",
                Decimal("2"),
                Decimal("0"),
            ),
        ],
    )
    w.execute(
        "CREATE TABLE postings(product_id VARCHAR,fulfilled_sales_eur DECIMAL(18,2),accepted_refunds_eur DECIMAL(18,2),net_revenue_eur DECIMAL(18,2),fulfilled_cogs_eur DECIMAL(18,2),recovered_cogs_eur DECIMAL(18,2),net_cogs_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2),posting_id VARCHAR,posting_date DATE)"
    )
    postings = [
        (
            "P1",
            Decimal("120"),
            Decimal("0"),
            Decimal("120"),
            Decimal("37"),
            Decimal("0"),
            Decimal("37"),
            Decimal("83"),
            "SALE:1",
            date(2026, 9, 1),
        ),
        (
            "P1",
            Decimal("0"),
            Decimal("2"),
            Decimal("-2"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("-2"),
            "RETURN:1",
            date(2025, 10, 3),
        ),
        (
            "P1",
            Decimal("0"),
            Decimal("1"),
            Decimal("-1"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("-1"),
            "RETURN:4",
            date(2025, 10, 5),
        ),
        (
            "P2",
            Decimal("100"),
            Decimal("0"),
            Decimal("100"),
            Decimal("90"),
            Decimal("0"),
            Decimal("90"),
            Decimal("10"),
            "SALE:2",
            date(2026, 9, 1),
        ),
        (
            "P3",
            Decimal("10"),
            Decimal("0"),
            Decimal("10"),
            Decimal("15"),
            Decimal("0"),
            Decimal("15"),
            Decimal("-5"),
            "SALE:3",
            date(2026, 9, 1),
        ),
        (
            "P5",
            Decimal("20"),
            Decimal("0"),
            Decimal("20"),
            Decimal("10"),
            Decimal("0"),
            Decimal("10"),
            Decimal("10"),
            "SALE:5",
            date(2026, 9, 1),
        ),
    ]
    w.executemany("INSERT INTO postings VALUES (?,?,?,?,?,?,?,?,?,?)", postings)
    w.execute("CREATE VIEW fact_financial_posting_historical AS SELECT * FROM postings")
    w.execute("ALTER TABLE fact_order_line ADD COLUMN posted_net_sales_eur DECIMAL(18,2) DEFAULT 0")
    w.execute("ALTER TABLE fact_order_line ADD COLUMN posted_cogs_eur DECIMAL(18,2) DEFAULT 0")
    w.execute("ALTER TABLE fact_order_line ADD COLUMN customer_sk_fulfilment BIGINT DEFAULT 1")
    for field in ("outbound_freight_eur", "returns_freight_eur", "contribution_after_freight_eur"):
        w.execute(f"ALTER TABLE postings ADD COLUMN {field} DECIMAL(18,2) DEFAULT 0")
    w.execute("UPDATE postings SET contribution_after_freight_eur=net_gross_profit_eur")
    w.execute(
        "UPDATE postings SET outbound_freight_eur=5,contribution_after_freight_eur=78 WHERE posting_id='SALE:1'"
    )
    w.execute(
        "UPDATE postings SET returns_freight_eur=1,contribution_after_freight_eur=-3 WHERE posting_id='RETURN:1'"
    )
    w.close()
    g = duckdb.connect(str(gold))
    g.execute(
        "CREATE TABLE mart_commercial_month(reporting_month DATE,home_country_code VARCHAR,product_id VARCHAR,fulfilled_sales_eur DECIMAL(18,2),accepted_refunds_eur DECIMAL(18,2),net_revenue_eur DECIMAL(18,2),fulfilled_cogs_eur DECIMAL(18,2),recovered_cogs_eur DECIMAL(18,2),net_cogs_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2))"
    )
    g.executemany(
        "INSERT INTO mart_commercial_month VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(p[-1].replace(day=1), "LT", *p[:8]) for p in postings],
    )
    for field in ("outbound_freight_eur", "returns_freight_eur", "contribution_after_freight_eur"):
        g.execute(f"ALTER TABLE mart_commercial_month ADD COLUMN {field} DECIMAL(18,2) DEFAULT 0")
    g.execute(
        "UPDATE mart_commercial_month SET contribution_after_freight_eur=net_gross_profit_eur"
    )
    g.execute(
        "UPDATE mart_commercial_month SET outbound_freight_eur=5,contribution_after_freight_eur=78 "
        "WHERE product_id='P1' AND reporting_month=DATE '2026-09-01'"
    )
    g.execute(
        "UPDATE mart_commercial_month SET returns_freight_eur=1,contribution_after_freight_eur=-3 "
        "WHERE product_id='P1' AND reporting_month=DATE '2025-10-01' AND net_revenue_eur=-2"
    )
    g.execute(
        "CREATE TABLE mart_company_month AS SELECT reporting_month,sum(fulfilled_sales_eur)::DECIMAL(18,2) fulfilled_sales_eur,sum(accepted_refunds_eur)::DECIMAL(18,2) accepted_refunds_eur,sum(net_revenue_eur)::DECIMAL(18,2) net_revenue_eur,sum(fulfilled_cogs_eur)::DECIMAL(18,2) fulfilled_cogs_eur,sum(recovered_cogs_eur)::DECIMAL(18,2) recovered_cogs_eur,sum(net_cogs_eur)::DECIMAL(18,2) net_cogs_eur,sum(net_gross_profit_eur)::DECIMAL(18,2) net_gross_profit_eur  ,sum(outbound_freight_eur)::DECIMAL(18,2) outbound_freight_eur,sum(returns_freight_eur)::DECIMAL(18,2) returns_freight_eur,sum(contribution_after_freight_eur)::DECIMAL(18,2) contribution_after_freight_eur FROM mart_commercial_month GROUP BY 1"
    )
    g.close()
    con = duckdb.connect(str(tmp_path / "product.duckdb"))
    con.execute(f"ATTACH '{wh}' AS warehouse (READ_ONLY)")
    con.execute(f"ATTACH '{gold}' AS gold (READ_ONLY)")
    con.execute(
        "CREATE TEMP TABLE build_lineage AS SELECT DATE '2022-01-01' period_start, DATE '2026-09-30' as_of_date, DATE '2026-09-01' as_of_month, DATE '2025-10-01' trailing_start_month, '8e33878d4c2719cc'::VARCHAR source_batch_id, 'a'::VARCHAR source_warehouse_manifest_sha256, 'b'::VARCHAR source_gold_manifest_sha256, 0.80::DECIMAL(9,6) abc_a,0.95::DECIMAL(9,6) abc_b,6::INTEGER xyz_min_months,0.50::DECIMAL(9,6) xyz_x,1.00::DECIMAL(9,6) xyz_y,1::INTEGER min_joint_baskets,0.01::DECIMAL(9,6) min_pair_support"
    )
    con.execute(SQL.read_text())
    yield con
    con.close()


def test_finance_abc_xyz_returns_and_reconciliation(miniature) -> None:
    con = miniature
    p1 = con.execute(
        "SELECT net_revenue_eur,net_gross_profit_eur,accepted_return_units,mature_return_unit_rate,contribution_after_freight_eur FROM mart_product_profitability WHERE product_id='P1'"
    ).fetchone()
    assert p1 == (Decimal("117.00"), Decimal("80.00"), 2, None, Decimal("74.00"))
    assert con.execute(
        "SELECT product_id,abc_class,xyz_class FROM mart_product_abc_xyz ORDER BY product_id"
    ).fetchall() == [
        ("P1", "A", "X"),
        ("P2", "B", "Z"),
        ("P3", "nonpositive", "zero_mean"),
        ("P4", "nonpositive", "insufficient_history"),
        ("P5", "B", "Z"),
    ]
    assert con.execute(
        "SELECT product_id,abc_cumulative_before,abc_cumulative_after_tie,"
        "eligible_months,trailing_units FROM mart_product_abc_xyz "
        "WHERE product_id IN ('P1','P2','P4','P5') ORDER BY product_id"
    ).fetchall() == [
        ("P1", Decimal("0.00000000"), Decimal("0.80000000"), 12, 14),
        ("P2", Decimal("0.80000000"), Decimal("1.00000000"), 12, 12),
        ("P4", None, None, 2, 0),
        ("P5", Decimal("0.80000000"), Decimal("1.00000000"), 12, 1),
    ]
    assert con.execute(
        "SELECT return_id,reason_group FROM mart_product_return_event ORDER BY return_id"
    ).fetchall() == [
        ("R1", "product_quality"),
        ("R2", "buyer_or_expectation"),
        ("R4", "buyer_or_expectation"),
        ("R5", "product_quality"),
    ]
    assert con.execute(
        "SELECT return_status,return_decision_date,accepted_quantity,posted_refund_ex_vat_eur,restocking_outcome_code FROM mart_product_return_event WHERE return_id='R5'"
    ).fetchone() == ("requested", None, None, Decimal("0.00"), None)
    assert all(v["violations"] == 0 for v in _validate(con).values())


def test_basket_deduplication_and_hand_calculated_lift(miniature) -> None:
    row = miniature.execute(
        "SELECT basket_count,item_a_baskets,item_b_baskets,joint_baskets,pair_support,confidence_a_to_b,confidence_b_to_a,lift FROM mart_basket_rules WHERE subcategory_a_id='S1' AND subcategory_b_id='S2'"
    ).fetchone()
    # 18 original distinct orders plus four basket orders. A occurs in 15, B in 3, jointly once.
    assert row == (
        18,
        15,
        3,
        1,
        Decimal("0.05555556"),
        Decimal("0.06666667"),
        Decimal("0.33333333"),
        Decimal("0.40000000"),
    )
    assert miniature.execute(
        "SELECT home_country_code,segment_code,joint_baskets,lift "
        "FROM mart_basket_context WHERE subcategory_a_id='S1' AND subcategory_b_id='S2'"
    ).fetchone() == ("LT", "CORE", 1, Decimal("0.40000000"))


def test_corrupt_upstream_checksum_is_rejected(tmp_path: Path) -> None:
    import json

    from baltic_sales_intelligence.config import load_configuration
    from baltic_sales_intelligence.product import build_product_intelligence
    from test_support.warehouse_fixtures import CONFIG_DIRECTORY

    wh = tmp_path / "database"
    wh.mkdir()
    (wh / "baltic_sales.duckdb").write_bytes(b"corrupt")
    config = load_configuration(CONFIG_DIRECTORY)
    (wh / "warehouse_manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "bsi.warehouse_publication",
                "schema_version": 1,
                "stage": "warehouse",
                "publication_status": "passed",
                "build_config_hash": config.deterministic_hash(),
                "source_batch_id": "8e33878d4c2719cc",
                "database": {"path": "baltic_sales.duckdb", "sha256": "0" * 64},
            }
        )
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        build_product_intelligence(config, wh, tmp_path / "gold", tmp_path / "product")
    assert not (tmp_path / "product").exists()


def test_atomic_publication_failure_restores_previous(tmp_path: Path, monkeypatch) -> None:
    import os

    from baltic_sales_intelligence.customer.builder import _publish_directory

    old = tmp_path / "product"
    staged = tmp_path / "staged"
    old.mkdir()
    staged.mkdir()
    (old / "publication.txt").write_text("previous")
    (staged / "publication.txt").write_text("candidate")
    real_replace = os.replace

    def fail_candidate(src, dst):
        if Path(src) == staged:
            raise OSError("simulated swap failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_candidate)
    with pytest.raises(OSError, match="simulated swap failure"):
        _publish_directory(staged, old, True)
    assert (old / "publication.txt").read_text() == "previous"


def test_product_cli_dry_run_routes_without_publication(tmp_path: Path, monkeypatch) -> None:
    from shutil import copytree
    from types import SimpleNamespace

    from baltic_sales_intelligence.pipeline.cli import ExitCode, main
    from test_support.warehouse_fixtures import CONFIG_DIRECTORY

    copytree(CONFIG_DIRECTORY, tmp_path / "config")
    called = {}

    def fake_build(configuration, warehouse, gold, output, *, publish, force):
        called.update(warehouse=warehouse, gold=gold, output=output, publish=publish, force=force)
        return SimpleNamespace(
            manifest={
                "objects": {"mart_product_abc_xyz": {"row_count": 4}},
                "source_batch_id": "8e33878d4c2719cc",
            }
        )

    monkeypatch.setattr(
        "baltic_sales_intelligence.pipeline.cli.build_product_intelligence", fake_build
    )
    result = main(["--config", str(tmp_path / "config"), "--stage", "product", "--dry-run"])
    assert result == ExitCode.OK
    assert called == {
        "warehouse": tmp_path / "data/database",
        "gold": tmp_path / "data/gold",
        "output": tmp_path / "data/product",
        "publish": False,
        "force": False,
    }


def test_deterministic_minature_rerun(miniature, tmp_path: Path) -> None:
    from baltic_sales_intelligence.product.builder import CONTRACTS

    paths = {name: path for _, name, path in miniature.execute("PRAGMA database_list").fetchall()}
    lineage = tmp_path / "lineage.parquet"
    miniature.execute(f"COPY build_lineage TO '{lineage}' (FORMAT PARQUET)")
    again = duckdb.connect(str(tmp_path / "second.duckdb"))
    try:
        again.execute(f"ATTACH '{paths['warehouse']}' AS warehouse (READ_ONLY)")
        again.execute(f"ATTACH '{paths['gold']}' AS gold (READ_ONLY)")
        again.execute(f"CREATE TEMP TABLE build_lineage AS SELECT * FROM read_parquet('{lineage}')")
        again.execute(SQL.read_text())
        for table, (_, key) in CONTRACTS.items():
            ordered = f"SELECT * FROM {table} ORDER BY {', '.join(key)}"
            assert miniature.execute(ordered).fetchall() == again.execute(ordered).fetchall()
    finally:
        again.close()


def test_miniature_builder_dry_run_and_atomic_publish(miniature, tmp_path: Path) -> None:
    import hashlib
    import json
    from shutil import copy2

    from baltic_sales_intelligence.config import load_configuration
    from baltic_sales_intelligence.product import build_product_intelligence
    from baltic_sales_intelligence.product.builder import CONTRACTS, REQUIRED
    from test_support.warehouse_fixtures import CONFIG_DIRECTORY

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    paths = {
        name: Path(path) for _, name, path in miniature.execute("PRAGMA database_list").fetchall()
    }
    warehouse_dir = tmp_path / "database"
    gold_dir = tmp_path / "gold"
    warehouse_dir.mkdir()
    gold_dir.mkdir()
    wh = warehouse_dir / "baltic_sales.duckdb"
    gp = gold_dir / "baltic_sales_gold.duckdb"
    copy2(paths["warehouse"], wh)
    copy2(paths["gold"], gp)
    with duckdb.connect(str(wh)) as con:
        for table in ("fact_order_line", "fact_return_event"):
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN source_batch_id VARCHAR DEFAULT '8e33878d4c2719cc'"
            )
    cfg = load_configuration(CONFIG_DIRECTORY)
    reporting = cfg.application.reporting
    common = {
        "period_start": str(reporting.period_start),
        "period_end": str(reporting.period_end),
        "operational_extension_end": str(reporting.extension_end),
        "default_as_of_date": str(reporting.default_as_of_date),
    }

    def objects(db: Path, names: dict[str, list[str]]) -> dict:
        with duckdb.connect(str(db), read_only=True) as con:
            result = {}
            for name in names:
                key = {
                    "dim_product": ["product_id"],
                    "dim_supplier": ["supplier_id"],
                    "dim_customer": ["customer_sk"],
                    "bridge_product_supplier": ["product_id"],
                    "dim_date": ["full_date"],
                    "fact_order_line": ["order_line_id"],
                    "fact_return_event": ["return_id"],
                    "fact_financial_posting_historical": ["posting_id"],
                    "mart_commercial_month": ["reporting_month", "product_id", "net_revenue_eur"],
                    "mart_company_month": ["reporting_month"],
                }[name]
                result[name] = {
                    "object_type": "view"
                    if name == "fact_financial_posting_historical"
                    else "table",
                    "grain": "miniature independent fixture",
                    "key": key,
                    "row_count": con.execute(f"SELECT count(*) FROM {name}").fetchone()[0],
                }
            return result

    wh_manifest = {
        "schema_id": "bsi.warehouse_publication",
        "schema_version": 1,
        "stage": "warehouse",
        "publication_status": "passed",
        "build_config_hash": cfg.deterministic_hash(),
        "source_batch_id": "8e33878d4c2719cc",
        "source_silver_manifest_sha256": "a" * 64,
        "reporting_window": {**common, "default_view": "fact_financial_posting_historical"},
        "database": {"path": wh.name, "sha256": sha(wh)},
        "objects": objects(wh, REQUIRED["warehouse"]),
    }
    wh_manifest_path = warehouse_dir / "warehouse_manifest.json"
    wh_manifest_path.write_text(json.dumps(wh_manifest))
    wh_hash = sha(wh_manifest_path)
    with duckdb.connect(str(gp)) as con:
        for table in ("mart_commercial_month", "mart_company_month"):
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN source_batch_id VARCHAR DEFAULT '8e33878d4c2719cc'"
            )
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN source_warehouse_manifest_sha256 VARCHAR "
                f"DEFAULT '{wh_hash}'"
            )
    gold_manifest = {
        "schema_id": "bsi.gold_publication",
        "schema_version": 1,
        "stage": "analytics",
        "publication_status": "passed",
        "build_config_hash": cfg.deterministic_hash(),
        "source_batch_id": "8e33878d4c2719cc",
        "source_silver_manifest_sha256": "a" * 64,
        "source_warehouse_manifest_sha256": wh_hash,
        "source_warehouse_database_sha256": sha(wh),
        "reporting_window": {**common, "default_boundary": "historical_only"},
        "database": {"path": gp.name, "sha256": sha(gp)},
        "objects": objects(gp, REQUIRED["gold"]),
    }
    (gold_dir / "gold_manifest.json").write_text(json.dumps(gold_manifest))
    output = tmp_path / "published_product"
    source_hashes = [sha(x) for x in (wh, wh_manifest_path, gp, gold_dir / "gold_manifest.json")]
    dry = build_product_intelligence(cfg, warehouse_dir, gold_dir, output, publish=False)
    assert dry.database_path is None and not output.exists()
    result = build_product_intelligence(cfg, warehouse_dir, gold_dir, output)
    assert result.database_path == output / "product_intelligence.duckdb"
    assert result.manifest["database"]["sha256"] == sha(result.database_path)
    assert set(result.manifest["objects"]) == set(CONTRACTS)
    assert all(value["violations"] == 0 for value in result.validations.values())
    replacement = build_product_intelligence(cfg, warehouse_dir, gold_dir, output, force=True)
    assert replacement.manifest["database"]["sha256"] == sha(replacement.database_path)
    assert (output / ".gitkeep").is_file()
    assert source_hashes == [
        sha(x) for x in (wh, wh_manifest_path, gp, gold_dir / "gold_manifest.json")
    ]
