# ruff: noqa: E501
"""Governed, read-only-input Milestone 10 product publication."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer.builder import (
    _has_material_output,
    _publish_directory,
    _validate_manifest_contracts,
)
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES

DATABASE_NAME = "product_intelligence.duckdb"
MANIFEST_NAME = "product_intelligence_manifest.json"
EXPECTED_BATCH = "8e33878d4c2719cc"
CONTRACTS = {
    "mart_product_month_country": (
        "month by home country by product",
        ["reporting_month", "home_country_code", "product_id"],
    ),
    "mart_product_profitability": ("product", ["product_id"]),
    "mart_product_abc_xyz": ("product", ["product_id"]),
    "mart_basket_rules": ("unordered subcategory pair", ["subcategory_a_id", "subcategory_b_id"]),
    "mart_basket_context": (
        "customer home country by fulfilment segment by unordered subcategory pair",
        ["home_country_code", "segment_code", "subcategory_a_id", "subcategory_b_id"],
    ),
    "mart_product_return_event": ("return event", ["return_id"]),
    "mart_product_return_quality": (
        "product by status by reason by disposition",
        ["product_id", "return_status", "return_reason_code", "restocking_outcome_code"],
    ),
}
REQUIRED = {
    "warehouse": {
        "dim_product": [
            "product_id",
            "sku",
            "product_name",
            "subcategory_name",
            "category_name",
            "launch_date",
            "discontinued_date",
            "lifecycle_status",
            "category_id",
            "subcategory_id",
        ],
        "dim_supplier": ["supplier_id", "supplier_name"],
        "dim_customer": ["customer_sk", "home_country_code", "segment_code"],
        "bridge_product_supplier": [
            "product_id",
            "supplier_id",
            "relationship_type",
            "valid_from",
            "valid_to",
        ],
        "dim_date": ["full_date", "month_start_date"],
        "fact_order_line": [
            "order_line_id",
            "order_id",
            "product_id",
            "order_status",
            "fulfilled_date",
            "quantity_ordered",
            "customer_sk_fulfilment",
            "posted_net_sales_eur",
            "posted_cogs_eur",
        ],
        "fact_return_event": [
            "return_id",
            "order_line_id",
            "product_id",
            "return_status",
            "return_requested_date",
            "return_decision_date",
            "accepted_quantity",
            "requested_quantity",
            "order_id",
            "return_reason_code",
            "restocking_outcome_code",
            "posted_refund_ex_vat_eur",
            "posted_recovered_cogs_eur",
        ],
        "fact_financial_posting_historical": [
            "posting_id",
            "product_id",
            "posting_date",
            "net_revenue_eur",
            "net_cogs_eur",
            "net_gross_profit_eur",
        ],
    },
    "gold": {
        "mart_commercial_month": [
            "reporting_month",
            "product_id",
            "home_country_code",
            "net_revenue_eur",
            "net_gross_profit_eur",
        ],
        "mart_company_month": ["reporting_month", "net_revenue_eur", "net_gross_profit_eur"],
    },
}
MONEY = (
    "fulfilled_sales_eur",
    "accepted_refunds_eur",
    "net_revenue_eur",
    "fulfilled_cogs_eur",
    "recovered_cogs_eur",
    "net_cogs_eur",
    "net_gross_profit_eur",
    "outbound_freight_eur",
    "returns_freight_eur",
    "contribution_after_freight_eur",
)


class ProductIntelligenceError(ValueError):
    """A publication contract or analytical acceptance check failed."""


@dataclass(frozen=True)
class ProductBuildResult:
    manifest: dict[str, Any]
    validations: dict[str, Any]
    database_path: Path | None = None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(condition: bool, name: str, validations: dict[str, Any], violations: int = 1) -> None:
    validations[name] = {
        "status": "passed" if condition else "failed",
        "violations": 0 if condition else violations,
    }
    if not condition:
        raise ProductIntelligenceError(f"product check failed: {name} ({violations})")


def _inspect_upstream(
    path: Path, manifest: dict[str, Any], required: dict[str, list[str]], label: str
) -> None:
    with duckdb.connect(str(path), read_only=True) as con:
        inventory = {
            name: kind
            for name, kind in con.execute(
                "SELECT table_name,table_type FROM information_schema.tables "
                "WHERE table_schema='main' AND table_catalog=current_database()"
            ).fetchall()
        }
        for name, columns in required.items():
            metadata = manifest.get("objects", {}).get(name)
            if not isinstance(metadata, dict):
                raise ProductIntelligenceError(f"{label} manifest object missing: {name}")
            rows = con.execute(f"PRAGMA table_info('{name}')").fetchall()
            available = {row[1]: row[2] for row in rows}
            money_columns = (
                MONEY
                if name
                in (
                    "fact_financial_posting_historical",
                    "mart_commercial_month",
                    "mart_company_month",
                )
                else ()
            )
            if not set(columns).union(money_columns) <= available.keys():
                raise ProductIntelligenceError(f"{label} schema mismatch: {name}")
            is_view = name == "fact_financial_posting_historical"
            if metadata.get("object_type") != ("view" if is_view else "table") or inventory.get(
                name
            ) != ("VIEW" if is_view else "BASE TABLE"):
                raise ProductIntelligenceError(f"{label} object type mismatch: {name}")
            if any(available[column] != "DECIMAL(18,2)" for column in money_columns):
                raise ProductIntelligenceError(f"{label} monetary type mismatch: {name}")
            if not metadata.get("grain") or not metadata.get("key"):
                raise ProductIntelligenceError(f"{label} object key/grain missing: {name}")
            if (
                metadata.get("row_count")
                != con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            ):
                raise ProductIntelligenceError(f"{label} row count mismatch: {name}")
            key = metadata["key"]
            if not set(key) <= available.keys():
                raise ProductIntelligenceError(f"{label} key columns missing: {name}")
            grouped = ", ".join(f'"{column}"' for column in key)
            duplicates = con.execute(
                f'SELECT count(*) FROM (SELECT {grouped} FROM "{name}" '
                f"GROUP BY {grouped} HAVING count(*)>1)"
            ).fetchone()[0]
            nulls = con.execute(
                f'SELECT count(*) FROM "{name}" WHERE '
                + " OR ".join(f'"{column}" IS NULL' for column in key)
            ).fetchone()[0]
            if duplicates or nulls:
                raise ProductIntelligenceError(f"{label} key violation: {name}")
        leaked = {
            row[0]
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='main'"
            ).fetchall()
        } & PRIVATE_LABEL_NAMES
        if leaked:
            raise ProductIntelligenceError(f"{label} private columns: {sorted(leaked)}")


def _check_row_lineage(wh_path: Path, gold_path: Path, batch: str, wh_hash: str) -> None:
    for path, table, columns, expected in (
        (wh_path, "fact_order_line", "source_batch_id", (batch,)),
        (wh_path, "fact_return_event", "source_batch_id", (batch,)),
        (
            gold_path,
            "mart_commercial_month",
            "source_batch_id, source_warehouse_manifest_sha256",
            (batch, wh_hash),
        ),
        (
            gold_path,
            "mart_company_month",
            "source_batch_id, source_warehouse_manifest_sha256",
            (batch, wh_hash),
        ),
    ):
        with duckdb.connect(str(path), read_only=True) as con:
            lineages = con.execute(f"SELECT DISTINCT {columns} FROM {table}").fetchall()
        if lineages != [expected]:
            raise ProductIntelligenceError(f"row lineage mismatch: {table}")


def _validate(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    def zero(name: str, sql: str) -> None:
        n = int(con.execute(sql).fetchone()[0])
        _check(n == 0, name, checks, n)

    for table, (_, key) in CONTRACTS.items():
        group = ",".join(f'"{k}"' for k in key)
        zero(
            f"{table}_duplicate_key",
            f"SELECT count(*) FROM (SELECT {group} FROM {table} GROUP BY {group} HAVING count(*)>1)",
        )
        # Nullable disposition is part of the return-quality grain.
        for column in key:
            if column != "restocking_outcome_code":
                zero(
                    f"{table}_{column}_null",
                    f'SELECT count(*) FROM {table} WHERE "{column}" IS NULL',
                )
    zero(
        "product_population",
        "SELECT abs((SELECT count(*) FROM mart_product_profitability)-(SELECT count(*) FROM warehouse.dim_product WHERE launch_date<=(SELECT as_of_date FROM build_lineage)))",
    )
    zero(
        "posting_product_orphan",
        "SELECT count(*) FROM warehouse.fact_financial_posting_historical x LEFT JOIN warehouse.dim_product p USING(product_id) WHERE p.product_id IS NULL",
    )
    zero(
        "primary_supplier_multiplication",
        "SELECT count(*) FROM (SELECT product_id FROM warehouse.bridge_product_supplier b,build_lineage l WHERE relationship_type='primary' AND valid_from<=l.as_of_date AND (valid_to IS NULL OR valid_to>l.as_of_date) GROUP BY product_id HAVING count(*)>1)",
    )
    zero(
        "financial_identity",
        "SELECT count(*) FROM mart_product_profitability WHERE net_revenue_eur!=fulfilled_sales_eur-accepted_refunds_eur OR net_cogs_eur!=fulfilled_cogs_eur-recovered_cogs_eur OR net_gross_profit_eur!=net_revenue_eur-net_cogs_eur OR contribution_after_freight_eur!=net_gross_profit_eur-outbound_freight_eur-returns_freight_eur OR (net_revenue_eur<=0 AND gross_margin_rate IS NOT NULL)",
    )
    zero(
        "abc_xyz_population",
        "SELECT abs((SELECT count(*) FROM mart_product_profitability)-(SELECT count(*) FROM mart_product_abc_xyz))",
    )
    zero(
        "abc_xyz_class",
        "SELECT count(*) FROM mart_product_abc_xyz WHERE abc_class NOT IN ('A','B','C','nonpositive') OR xyz_class NOT IN ('X','Y','Z','zero_mean','insufficient_history') OR (portfolio_class IS NOT NULL AND portfolio_class!=abc_class||xyz_class)",
    )
    zero(
        "abc_positive_total",
        "SELECT count(*) FROM mart_product_abc_xyz WHERE trailing_gp_eur>0 AND (positive_total_eur IS NULL OR positive_total_eur<=0 OR abc_share IS NULL)",
    )
    zero(
        "abc_denominator_and_share",
        """WITH totals AS (
          SELECT coalesce(sum(trailing_gp_eur) FILTER(WHERE trailing_gp_eur>0),0) gp
          FROM mart_product_abc_xyz
        ) SELECT count(*) FROM mart_product_abc_xyz x,totals t
          WHERE coalesce(x.positive_total_eur,0)!=t.gp
             OR (x.trailing_gp_eur>0 AND abs(x.abc_share-
                 round(x.trailing_gp_eur::DECIMAL(38,8)/t.gp,8))>0.00000001)
             OR (x.trailing_gp_eur<=0 AND x.abc_class!='nonpositive')""",
    )
    zero(
        "abc_tie_class",
        "SELECT count(*) FROM (SELECT trailing_gp_eur FROM mart_product_abc_xyz WHERE trailing_gp_eur>0 GROUP BY 1 HAVING count(DISTINCT abc_class)>1)",
    )
    zero(
        "xyz_zero_history",
        "SELECT count(*) FROM mart_product_abc_xyz WHERE (eligible_months<6 AND xyz_class!='insufficient_history') OR (eligible_months>=6 AND trailing_units=0 AND xyz_class!='zero_mean')",
    )
    zero(
        "basket_counts",
        "SELECT count(*) FROM mart_basket_rules WHERE joint_baskets>item_a_baskets OR joint_baskets>item_b_baskets OR item_a_baskets>basket_count OR item_b_baskets>basket_count OR subcategory_a_id>=subcategory_b_id",
    )
    zero(
        "basket_formulas",
        """SELECT count(*) FROM mart_basket_rules WHERE
           abs(item_a_support-item_a_baskets::DECIMAL(38,8)/basket_count)>0.00000001 OR
           abs(item_b_support-item_b_baskets::DECIMAL(38,8)/basket_count)>0.00000001 OR
           abs(pair_support-joint_baskets::DECIMAL(38,8)/basket_count)>0.00000001 OR
           abs(confidence_a_to_b-joint_baskets::DECIMAL(38,8)/item_a_baskets)>0.00000001 OR
           abs(confidence_b_to_a-joint_baskets::DECIMAL(38,8)/item_b_baskets)>0.00000001 OR
           abs(lift-joint_baskets::DECIMAL(38,8)*basket_count/
               (item_a_baskets*item_b_baskets))>0.00000001""",
    )
    zero(
        "basket_context_counts",
        """SELECT count(*) FROM mart_basket_context WHERE
           joint_baskets>item_a_baskets OR joint_baskets>item_b_baskets OR
           item_a_baskets>basket_count OR item_b_baskets>basket_count OR
           subcategory_a_id>=subcategory_b_id OR
           context_status NOT IN ('threshold_met','sparse_context')""",
    )
    zero(
        "basket_context_formulas",
        """SELECT count(*) FROM mart_basket_context WHERE
           abs(pair_support-joint_baskets::DECIMAL(38,8)/basket_count)>0.00000001 OR
           abs(confidence_a_to_b-joint_baskets::DECIMAL(38,8)/item_a_baskets)>0.00000001 OR
           abs(confidence_b_to_a-joint_baskets::DECIMAL(38,8)/item_b_baskets)>0.00000001 OR
           abs(lift-joint_baskets::DECIMAL(38,8)*basket_count/
               (item_a_baskets*item_b_baskets))>0.00000001""",
    )
    zero(
        "return_cap",
        "SELECT count(*) FROM (SELECT s.order_line_id FROM warehouse.fact_order_line s JOIN warehouse.fact_return_event r USING(order_line_id) CROSS JOIN build_lineage l WHERE r.return_status='accepted' AND r.return_decision_date<=l.as_of_date GROUP BY s.order_line_id,s.quantity_ordered HAVING sum(r.accepted_quantity)>s.quantity_ordered)",
    )
    zero(
        "return_event_status",
        "SELECT count(*) FROM mart_product_return_event WHERE (return_status='accepted' AND (accepted_quantity IS NULL OR return_decision_date IS NULL)) OR (return_status!='accepted' AND (accepted_quantity IS NOT NULL OR posted_refund_ex_vat_eur!=0))",
    )
    zero(
        "cutoff",
        "SELECT count(*) FROM mart_product_return_event,build_lineage WHERE return_requested_date>build_lineage.as_of_date OR return_decision_date>build_lineage.as_of_date",
    )
    zero(
        "posting_cutoff",
        "SELECT count(*) FROM warehouse.fact_financial_posting_historical,build_lineage WHERE posting_date NOT BETWEEN period_start AND as_of_date",
    )
    zero(
        "accepted_return_event_population",
        """SELECT abs((SELECT count(*) FROM mart_product_return_event WHERE return_status='accepted')-
                   (SELECT count(*) FROM warehouse.fact_return_event,build_lineage
                    WHERE return_status='accepted' AND return_decision_date BETWEEN period_start AND as_of_date))""",
    )
    zero(
        "return_quality_reconciliation",
        """SELECT abs((SELECT count(*) FROM mart_product_return_event)-
                   (SELECT coalesce(sum(return_event_count),0) FROM mart_product_return_quality))
            + abs((SELECT coalesce(sum(accepted_quantity),0) FROM mart_product_return_event)-
                  (SELECT coalesce(sum(accepted_units),0) FROM mart_product_return_quality))""",
    )
    event_refunds = con.execute(
        "SELECT coalesce(sum(posted_refund_ex_vat_eur),0) FROM mart_product_return_event"
    ).fetchone()[0]
    grouped_refunds = con.execute(
        "SELECT coalesce(sum(posted_refund_eur),0) FROM mart_product_return_quality"
    ).fetchone()[0]
    _check(event_refunds == grouped_refunds, "return_quality_refund_reconciliation", checks)
    for field in MONEY:
        source = con.execute(
            f"SELECT coalesce(sum({field}),0)::DECIMAL(18,2) FROM warehouse.fact_financial_posting_historical"
        ).fetchone()[0]
        product = con.execute(
            f"SELECT coalesce(sum({field}),0)::DECIMAL(18,2) FROM mart_product_profitability"
        ).fetchone()[0]
        monthly = con.execute(
            f"SELECT coalesce(sum({field}),0)::DECIMAL(18,2) FROM gold.mart_company_month"
        ).fetchone()[0]
        _check(source == product == monthly, f"{field}_reconciliation", checks)
        checks[f"{field}_reconciliation"]["warehouse_eur"] = str(source)
        checks[f"{field}_reconciliation"]["product_eur"] = str(product)
        checks[f"{field}_reconciliation"]["gold_eur"] = str(monthly)
    for field in MONEY:
        month = con.execute(
            f"SELECT coalesce(sum({field}),0)::DECIMAL(18,2) FROM mart_product_month_country"
        ).fetchone()[0]
        product = con.execute(
            f"SELECT coalesce(sum({field}),0)::DECIMAL(18,2) FROM mart_product_profitability"
        ).fetchone()[0]
        _check(month == product, f"{field}_monthly_reconciliation", checks)
    accepted_units = con.execute(
        "SELECT coalesce(sum(accepted_quantity),0) FROM warehouse.fact_return_event,build_lineage WHERE return_status='accepted' AND return_decision_date BETWEEN period_start AND as_of_date"
    ).fetchone()[0]
    output_units = con.execute(
        "SELECT coalesce(sum(accepted_return_units),0) FROM mart_product_profitability"
    ).fetchone()[0]
    _check(accepted_units == output_units, "accepted_units_reconciliation", checks)
    output_columns = {
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='main' AND table_catalog=current_database()"
        ).fetchall()
    }
    _check(not output_columns & PRIVATE_LABEL_NAMES, "private_metadata", checks)
    return checks


def build_product_intelligence(
    configuration: ProjectConfiguration,
    warehouse_directory: Path,
    gold_directory: Path,
    output_directory: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> ProductBuildResult:
    """Validate lineage, materialise product analyses, then atomically publish."""
    wh, wh_hash, wh_path, gold, gold_hash, gold_path = _validate_manifest_contracts(
        configuration, Path(warehouse_directory), Path(gold_directory)
    )
    if wh["source_batch_id"] != EXPECTED_BATCH:
        raise ProductIntelligenceError("unexpected source batch")
    _inspect_upstream(wh_path, wh, REQUIRED["warehouse"], "warehouse")
    _inspect_upstream(gold_path, gold, REQUIRED["gold"], "Gold")
    _check_row_lineage(wh_path, gold_path, wh["source_batch_id"], wh_hash)
    if publish and _has_material_output(Path(output_directory)) and not force:
        raise ProductIntelligenceError("product output exists; use --force")
    output_directory = Path(output_directory)
    if publish:
        output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-",
        dir=output_directory.parent if publish else None,
    ) as tmp:
        staged = Path(tmp) / output_directory.name
        staged.mkdir()
        (staged / ".gitkeep").touch()
        db = staged / DATABASE_NAME
        con = duckdb.connect(str(db))
        try:
            con.execute("PRAGMA threads=1")
            con.execute(
                f"ATTACH '{str(wh_path.resolve()).replace(chr(39), chr(39) * 2)}' AS warehouse (READ_ONLY)"
            )
            con.execute(
                f"ATTACH '{str(gold_path.resolve()).replace(chr(39), chr(39) * 2)}' AS gold (READ_ONLY)"
            )
            r = configuration.application.reporting
            a = configuration.analytics.product
            # Months are complete at the approved cutoff; the trailing window is inclusive.
            trailing = date(r.default_as_of_date.year - 1, r.default_as_of_date.month % 12 + 1, 1)
            con.execute(
                """CREATE TEMP TABLE build_lineage AS SELECT
                ?::DATE period_start, ?::DATE as_of_date, date_trunc('month',?::DATE)::DATE as_of_month,
                ?::DATE trailing_start_month, ?::VARCHAR source_batch_id,
                ?::VARCHAR source_warehouse_manifest_sha256, ?::VARCHAR source_gold_manifest_sha256,
                ?::DECIMAL(9,6) abc_a, ?::DECIMAL(9,6) abc_b,
                ?::INTEGER xyz_min_months, ?::DECIMAL(9,6) xyz_x, ?::DECIMAL(9,6) xyz_y,
                30::INTEGER min_joint_baskets, 0.01::DECIMAL(9,6) min_pair_support""",
                [
                    r.period_start,
                    r.default_as_of_date,
                    r.default_as_of_date,
                    trailing,
                    wh["source_batch_id"],
                    wh_hash,
                    gold_hash,
                    Decimal(str(a.abc_a_cumulative_share)),
                    Decimal(str(a.abc_b_cumulative_share)),
                    a.xyz_minimum_history_months,
                    Decimal(str(a.xyz_x_max_cv)),
                    Decimal(str(a.xyz_y_max_cv)),
                ],
            )
            con.execute(
                (Path(__file__).parents[3] / "sql/analysis/product_intelligence.sql").read_text()
            )
            checks = _validate(con)
            checks["upstream_integrity"] = {
                "status": "passed",
                "violations": 0,
                "objects_verified": sum(map(len, REQUIRED.values())),
                "source_batch_id": wh["source_batch_id"],
            }
            objects = {}
            policies = {
                "mart_product_month_country": "posted finance by month and customer home country",
                "mart_product_profitability": (
                    "posted finance and allocated freight; supplier approval at cutoff; "
                    "mature return rate unestimated"
                ),
                "mart_product_abc_xyz": "positive GP 80/95%; monthly unit CV 0.5/1.0, six months",
                "mart_basket_rules": "fulfilled order, distinct subcategory; 30 joint orders, 1% support",
                "mart_basket_context": (
                    "global eligible pairs by home country and fulfilment segment; "
                    "sparse context explicitly flagged"
                ),
                "mart_product_return_event": "return request and decision state as of cutoff",
                "mart_product_return_quality": "as-of event reason and disposition groupings",
            }
            for name, (grain, key) in CONTRACTS.items():
                objects[name] = {
                    "schema_id": f"bsi.product.{name}",
                    "dataset_name": name,
                    "schema_version": 1,
                    "object_type": "table",
                    "grain": grain,
                    "key": key,
                    "row_count": con.execute(f"SELECT count(*) FROM {name}").fetchone()[0],
                    "columns": [
                        {"name": x[1], "type": x[2], "nullable": not x[3]}
                        for x in con.execute(f"PRAGMA table_info('{name}')").fetchall()
                    ],
                    "as_of_date": str(r.default_as_of_date),
                    "observation_window": (
                        f"{trailing}..{r.default_as_of_date}"
                        if name == "mart_product_abc_xyz"
                        else f"{r.period_start}..{r.default_as_of_date}"
                    ),
                    "currency": "EUR",
                    "units": "EUR posted cents for money; integer units and counts; ratios unitless",
                    "policy": policies[name],
                    "method_version": "product-intelligence-v1",
                    "source": "validated warehouse and Gold publications",
                }
            con.execute("DETACH gold")
            con.execute("DETACH warehouse")
            con.execute("CHECKPOINT")
        except duckdb.Error as exc:
            raise ProductIntelligenceError(f"product SQL failed: {exc}") from exc
        finally:
            con.close()
        manifest = {
            "schema_id": "bsi.product_intelligence_publication",
            "schema_version": 1,
            "stage": "product",
            "pipeline_version": __version__,
            "publication_status": "passed",
            "source_batch_id": wh["source_batch_id"],
            "source_warehouse_manifest_sha256": wh_hash,
            "source_warehouse_database_sha256": wh["database"]["sha256"],
            "source_gold_manifest_sha256": gold_hash,
            "source_gold_database_sha256": gold["database"]["sha256"],
            "source_silver_manifest_sha256": wh["source_silver_manifest_sha256"],
            "build_config_hash": configuration.deterministic_hash(),
            "stage_duration_seconds": 0.0,
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {"duckdb": importlib.metadata.version("duckdb")},
            },
            "database": {"path": DATABASE_NAME, "sha256": _sha(db)},
            "objects": objects,
            "analysis_window": {
                "period_start": str(r.period_start),
                "as_of_date": str(r.default_as_of_date),
                "trailing_start_month": str(trailing),
                "post_cutoff_excluded": True,
            },
            "policies": {
                "abc": "positive trailing-12-month GP; equal-GP groups use preceding cumulative share; nonpositive separate",
                "xyz": "population CV of fulfilled monthly units; complete post-launch, pre-discontinuation months; returns excluded",
                "basket": "fulfilled order, distinct subcategory, unordered pair; 30 joint orders and 1% support",
                "return_rate": "unestimated: no approved cohort maturity duration",
                "supplier": "primary approval at cutoff is descriptive, actual transaction source unknown",
            },
            "validations": checks,
        }
        (staged / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if not publish:
            return ProductBuildResult(manifest, checks)
        _publish_directory(staged, output_directory, force)
    return ProductBuildResult(manifest, checks, output_directory / DATABASE_NAME)
