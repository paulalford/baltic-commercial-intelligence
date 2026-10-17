"""Publish SPEC-14 opportunities and deterministic management insights."""

# ruff: noqa: E501  # Manifest method descriptions are intentionally explicit.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer.builder import (
    _has_material_output,
    _publish_directory,
    _validate_manifest,
    _validate_manifest_contracts,
)
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.insights.core import evaluate_insights
from baltic_sales_intelligence.opportunities.core import build_opportunities
from baltic_sales_intelligence.opportunities.policy import OpportunityPolicy
from baltic_sales_intelligence.opportunities.sources import load_facts

DATABASE_NAME = "opportunity_insights.duckdb"
MANIFEST_NAME = "opportunity_insights_manifest.json"
SOURCE_SPEC = {
    "customer": (
        "customer_analytics_manifest.json",
        "customer",
        "bsi.customer_analytics_publication",
        "customer_analytics.duckdb",
    ),
    "product": (
        "product_intelligence_manifest.json",
        "product",
        "bsi.product_intelligence_publication",
        "product_intelligence.duckdb",
    ),
    "statistics": (
        "statistical_analysis_manifest.json",
        "statistics",
        "bsi.statistical_analysis_publication",
        "statistical_analysis.duckdb",
    ),
    "forecasting": (
        "forecasting_anomalies_manifest.json",
        "forecasting",
        "bsi.forecasting_anomalies_publication",
        "forecasting_anomalies.duckdb",
    ),
}
REQUIRED = {
    "warehouse": {
        "fact_financial_posting_historical": {
            "posting_date",
            "net_revenue_eur",
            "net_gross_profit_eur",
        },
        "fact_order_line": {"fulfilled_date", "quantity_ordered", "posted_gross_profit_eur"},
        "dim_product": {"product_id", "category_id"},
    },
    "gold": {
        "mart_commercial_month": {"reporting_month", "net_revenue_eur", "net_gross_profit_eur"},
        "mart_company_month": {"reporting_month", "net_revenue_eur", "net_gross_profit_eur"},
    },
    "customer": {
        "mart_customer_value": {"customer_id", "historical_value_net_gross_profit_eur"},
        "mart_rfm": {"customer_id", "is_lapsed_buyer"},
        "mart_cohort_retention": {"cohort_month", "retention_rate"},
    },
    "product": {
        "mart_basket_context": {"subcategory_a_id", "subcategory_b_id"},
        "mart_product_return_event": {"return_id", "reason_group"},
    },
    "statistics": {"basket_pair_result": {"subcategory_a_id", "subcategory_b_id", "lift"}},
    "forecasting": {
        "sales_monthly": {"reporting_month", "net_revenue_eur", "net_gross_profit_eur"},
        "walkforward_prediction": {"origin_month", "target_month", "interval95_high_eur"},
        "model_evaluation": {"coverage95"},
    },
}
WAREHOUSE_TYPES = {
    "fact_financial_posting_historical": {
        "posting_date": "DATE",
        "net_revenue_eur": "DECIMAL(18,2)",
        "net_gross_profit_eur": "DECIMAL(18,2)",
    },
    "fact_order_line": {
        "fulfilled_date": "DATE",
        "quantity_ordered": "BIGINT",
        "posted_gross_profit_eur": "DECIMAL(18,2)",
    },
    "dim_product": {"product_id": "VARCHAR", "category_id": "VARCHAR"},
}


class OpportunityContractError(ValueError):
    """Governed source or result contract failed."""


@dataclass(frozen=True)
class OpportunityBuildResult:
    manifest: dict[str, Any]
    validations: dict[str, Any]
    output_directory: Path | None = None
    database_path: Path | None = None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sources(configuration: ProjectConfiguration, data_root: Path):
    """Verify checksums, batch, declared schema and the lineage graph before use."""
    wh, wh_hash, wh_db, gold, gold_hash, gold_db = _validate_manifest_contracts(
        configuration, data_root / "database", data_root / "gold"
    )
    sources = {"warehouse": (wh, wh_hash, wh_db), "gold": (gold, gold_hash, gold_db)}
    for label, (manifest_name, stage, schema, db_name) in SOURCE_SPEC.items():
        sources[label] = _validate_manifest(
            configuration, data_root / label, manifest_name, stage, schema, db_name
        )
    if (
        str(configuration.application.reporting.period_start) != "2022-01-01"
        or str(configuration.application.reporting.default_as_of_date) != "2026-09-30"
    ):
        raise OpportunityContractError("approved reporting window is required")
    batch = wh["source_batch_id"]
    hashes = {label: source[1] for label, source in sources.items()}
    db_hashes = {label: source[0]["database"]["sha256"] for label, source in sources.items()}
    silver = wh["source_silver_manifest_sha256"]
    for label, (manifest, _, db) in sources.items():
        if manifest.get("source_batch_id") != batch:
            raise OpportunityContractError(f"{label} batch mismatch")
        window = manifest.get("analysis_window") if label not in {"warehouse", "gold"} else None
        if label not in {"warehouse", "gold"} and (
            not isinstance(window, dict)
            or window.get("as_of_date") != "2026-09-30"
            or window.get("period_start") != "2022-01-01"
            or not window.get("post_cutoff_excluded")
        ):
            raise OpportunityContractError(f"{label} window mismatch")
        if label in {"customer", "product"}:
            for parent in ("warehouse", "gold"):
                if (
                    manifest.get(f"source_{parent}_manifest_sha256") != hashes[parent]
                    or manifest.get(f"source_{parent}_database_sha256") != db_hashes[parent]
                ):
                    raise OpportunityContractError(f"{label} {parent} lineage mismatch")
        if label in {"statistics", "forecasting"}:
            parents = {"warehouse", "gold", "product"}
            if label == "statistics":
                parents.add("customer")
            if manifest.get("source_manifests_sha256") != {
                p: hashes[p] for p in parents
            } or manifest.get("source_databases_sha256") != {p: db_hashes[p] for p in parents}:
                raise OpportunityContractError(f"{label} source lineage mismatch")
        if (
            label not in {"warehouse", "gold"}
            and manifest.get("source_silver_manifest_sha256") != silver
        ):
            raise OpportunityContractError(f"{label} Silver lineage mismatch")
        with duckdb.connect(str(db), read_only=True) as con:
            for table, required in REQUIRED[label].items():
                metadata = manifest.get("objects", {}).get(table)
                if (
                    not isinstance(metadata, dict)
                    or not metadata.get("grain")
                    or not metadata.get("key")
                    or metadata.get("object_type") not in {"table", "view"}
                ):
                    raise OpportunityContractError(f"{label}.{table} manifest grain/key missing")
                expected_type = "VIEW" if metadata.get("object_type") == "view" else "BASE TABLE"
                inventory = con.execute(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_catalog=current_database() AND table_schema='main' "
                    "AND table_name=?",
                    [table],
                ).fetchone()
                if inventory is None or inventory[0] != expected_type:
                    raise OpportunityContractError(f"{label}.{table} object type mismatch")
                actual = {
                    x[1]: x[2] for x in con.execute(f"PRAGMA table_info('{table}')").fetchall()
                }
                declared = (
                    {x["name"]: x["type"] for x in metadata["columns"]}
                    if "columns" in metadata
                    else WAREHOUSE_TYPES.get(table, {})
                )
                if (
                    not required <= actual.keys()
                    or actual.keys() & PRIVATE_LABEL_NAMES
                    or any(actual[column] != declared.get(column) for column in required)
                ):
                    raise OpportunityContractError(f"{label}.{table} schema/private-label mismatch")
                n = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if n != metadata.get("row_count"):
                    raise OpportunityContractError(f"{label}.{table} row-count mismatch")
                key = metadata["key"]
                if not isinstance(key, list) or not key or not set(key) <= actual.keys():
                    raise OpportunityContractError(f"{label}.{table} key columns mismatch")
                quoted = ",".join(f'"{column}"' for column in key)
                duplicates = con.execute(
                    f'SELECT count(*) FROM (SELECT {quoted} FROM "{table}" '
                    f"GROUP BY {quoted} HAVING count(*)>1)"
                ).fetchone()[0]
                nulls = con.execute(
                    f'SELECT count(*) FROM "{table}" WHERE '
                    + " OR ".join(f'"{column}" IS NULL' for column in key)
                ).fetchone()[0]
                if duplicates or nulls:
                    raise OpportunityContractError(f"{label}.{table} duplicate or null key")
    return sources


def _create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE TABLE opportunity (
      opportunity_id VARCHAR PRIMARY KEY,opportunity_code VARCHAR,as_of_date DATE,
      observation_start_date DATE,observation_end_date DATE,
      business_entity VARCHAR,entity_id VARCHAR,status VARCHAR,problem_or_potential VARCHAR,
      evidence_metric_ids VARCHAR[],baseline VARCHAR,estimated_impact_low DECIMAL(18,2),
      estimated_impact_base DECIMAL(18,2),estimated_impact_high DECIMAL(18,2),
      impact_unit VARCHAR,estimation_formula VARCHAR,assumptions VARCHAR,
      limitation VARCHAR,recommended_next_step VARCHAR,owner_role VARCHAR,method_version VARCHAR,
      support_count BIGINT,observation_months INTEGER,measurement_quality DOUBLE,
      overlap_ids VARCHAR[],impact_score DOUBLE,confidence_score DOUBLE,
      actionability_score DOUBLE,urgency_score DOUBLE,score DOUBLE,must_not_sum BOOLEAN)""")
    con.execute("""CREATE TABLE opportunity_overlap (
      opportunity_id_a VARCHAR,opportunity_id_b VARCHAR,overlap_id VARCHAR,
      must_not_sum BOOLEAN,PRIMARY KEY(opportunity_id_a,opportunity_id_b,overlap_id))""")
    con.execute("""CREATE TABLE opportunity_suppression (
      reason VARCHAR PRIMARY KEY,suppressed_count BIGINT)""")
    con.execute("""CREATE TABLE management_insight (
      insight_id VARCHAR PRIMARY KEY,rule_id VARCHAR,scope_code VARCHAR,
      as_of_date DATE,observation_start_date DATE,observation_end_date DATE,
      status VARCHAR,severity VARCHAR,numerator DOUBLE,denominator DOUBLE,
      threshold DOUBLE,trigger_condition VARCHAR,support_count BIGINT,
      evidence_metric_ids VARCHAR[],independent_evidence VARCHAR,
      management_interpretation VARCHAR,recommended_next_step VARCHAR,
      limitation VARCHAR,method_version VARCHAR)""")


def _insert(con: duckdb.DuckDBPyConnection, table: str, keys: tuple[str, ...], rows: list[dict]):
    placeholders = ",".join("?" for _ in keys)
    con.executemany(
        f'INSERT INTO "{table}" VALUES ({placeholders})',
        [[row[key] for key in keys] for row in rows],
    ) if rows else None


OPPORTUNITY_KEYS = (
    "opportunity_id",
    "opportunity_code",
    "as_of_date",
    "observation_start_date",
    "observation_end_date",
    "business_entity",
    "entity_id",
    "status",
    "problem_or_potential",
    "evidence_metric_ids",
    "baseline",
    "estimated_impact_low",
    "estimated_impact_base",
    "estimated_impact_high",
    "impact_unit",
    "estimation_formula",
    "assumptions",
    "limitation",
    "recommended_next_step",
    "owner_role",
    "method_version",
    "support_count",
    "observation_months",
    "measurement_quality",
    "overlap_ids",
    "impact_score",
    "confidence_score",
    "actionability_score",
    "urgency_score",
    "score",
    "must_not_sum",
)
INSIGHT_KEYS = (
    "insight_id",
    "rule_id",
    "scope_code",
    "as_of_date",
    "observation_start_date",
    "observation_end_date",
    "status",
    "severity",
    "numerator",
    "denominator",
    "threshold",
    "trigger_condition",
    "support_count",
    "evidence_metric_ids",
    "independent_evidence",
    "management_interpretation",
    "recommended_next_step",
    "limitation",
    "method_version",
)


def _validate_finance(con: duckdb.DuckDBPyConnection, cutoff: date) -> None:
    bad = con.execute(
        """WITH gold_month AS (
      SELECT reporting_month,sum(net_revenue_eur)::DECIMAL(18,2) rev,
        sum(net_gross_profit_eur)::DECIMAL(18,2) gp
      FROM gold.mart_commercial_month WHERE reporting_month<=? GROUP BY 1),
      forecast_month AS (SELECT * FROM forecasting.sales_monthly
        WHERE scope_code='COMPANY')
      SELECT count(*) FROM gold_month g FULL JOIN forecast_month f
      ON f.reporting_month=g.reporting_month
      WHERE g.reporting_month IS NULL OR f.reporting_month IS NULL
         OR g.rev<>f.net_revenue_eur OR g.gp<>f.net_gross_profit_eur""",
        [cutoff],
    ).fetchone()[0]
    if bad:
        raise OpportunityContractError("Gold and forecast actual financial totals differ")
    warehouse_bad = con.execute(
        """WITH posted AS (
      SELECT date_trunc('month',posting_date)::DATE reporting_month,
        sum(net_revenue_eur)::DECIMAL(18,2) rev,
        sum(net_gross_profit_eur)::DECIMAL(18,2) gp
      FROM warehouse.fact_financial_posting_historical
      WHERE posting_date<=? GROUP BY 1),
      forecast_month AS (SELECT * FROM forecasting.sales_monthly
        WHERE scope_code='COMPANY')
      SELECT count(*) FROM posted p FULL JOIN forecast_month f
      ON f.reporting_month=p.reporting_month
      WHERE p.reporting_month IS NULL OR f.reporting_month IS NULL
         OR p.rev<>f.net_revenue_eur OR p.gp<>f.net_gross_profit_eur""",
        [cutoff],
    ).fetchone()[0]
    if warehouse_bad:
        raise OpportunityContractError("Warehouse and forecast actual financial totals differ")
    future = con.execute(
        """SELECT count(*) FROM gold.mart_commercial_month
      WHERE reporting_month>?""",
        [cutoff],
    ).fetchone()[0]
    if future:
        raise OpportunityContractError("post-cutoff Gold rows are present")


def _validate_result(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    bad = con.execute(
        """SELECT count(*) FROM opportunity WHERE
      opportunity_id IS NULL OR opportunity_code IS NULL OR entity_id IS NULL
      OR as_of_date IS NULL OR observation_start_date IS NULL
      OR observation_end_date IS NULL OR baseline IS NULL
      OR evidence_metric_ids IS NULL OR assumptions IS NULL
      OR status IS NULL OR score IS NULL OR as_of_date<>?
      OR observation_end_date<>as_of_date
      OR observation_start_date>observation_end_date OR score NOT BETWEEN 0 AND 100 OR
      impact_score NOT BETWEEN 0 AND 100 OR confidence_score NOT BETWEEN 0 AND 100 OR
      actionability_score NOT BETWEEN 0 AND 100 OR urgency_score NOT BETWEEN 0 AND 100 OR
      status NOT IN ('estimable','investigative') OR
      (status='estimable' AND (estimated_impact_low IS NULL OR estimated_impact_base IS NULL
       OR estimated_impact_high IS NULL OR estimated_impact_low<0
       OR estimated_impact_low>estimated_impact_base
       OR estimated_impact_base>estimated_impact_high
       OR impact_unit IS NULL OR estimation_formula IS NULL)) OR
      (status='investigative' AND (estimated_impact_low IS NOT NULL
       OR estimated_impact_base IS NOT NULL OR estimated_impact_high IS NOT NULL
       OR impact_unit IS NOT NULL OR estimation_formula IS NOT NULL))""",
        [cutoff],
    ).fetchone()[0]
    if bad:
        raise OpportunityContractError("opportunity ordering/status/scoring contract violated")
    bad = con.execute(
        """SELECT count(*) FROM management_insight WHERE
      insight_id IS NULL OR rule_id IS NULL OR scope_code IS NULL
      OR as_of_date IS NULL OR observation_start_date IS NULL OR observation_end_date IS NULL
      OR support_count IS NULL OR threshold IS NULL OR trigger_condition IS NULL
      OR evidence_metric_ids IS NULL OR independent_evidence IS NULL OR status IS NULL
      OR as_of_date<>? OR observation_end_date>? OR observation_start_date>observation_end_date
      OR rule_id NOT IN ('R01','R02','R03','R04','R05','R06','R07')
      OR status NOT IN ('triggered','not_triggered','insufficient_evidence')
      OR (status='triggered' AND (severity NOT IN ('investigate','high')
        OR severity IS NULL OR management_interpretation IS NULL
        OR recommended_next_step IS NULL))
      OR (status<>'triggered' AND severity IS NOT NULL)
      OR (status='insufficient_evidence' AND (numerator IS NOT NULL OR denominator IS NOT NULL))""",
        [cutoff, cutoff],
    ).fetchone()[0]
    if bad:
        raise OpportunityContractError("management insight contract violated")
    bad = con.execute("""SELECT count(*) FROM opportunity_overlap x
      LEFT JOIN opportunity a ON a.opportunity_id=x.opportunity_id_a
      LEFT JOIN opportunity b ON b.opportunity_id=x.opportunity_id_b
      WHERE a.opportunity_id IS NULL OR b.opportunity_id IS NULL
         OR NOT a.must_not_sum OR NOT b.must_not_sum OR NOT x.must_not_sum""").fetchone()[0]
    if bad:
        raise OpportunityContractError("overlap relationship contract violated")
    return {
        "status": "passed",
        "violations": 0,
        "opportunities": con.execute("SELECT count(*) FROM opportunity").fetchone()[0],
        "insights": con.execute("SELECT count(*) FROM management_insight").fetchone()[0],
    }


def _objects(con: duckdb.DuckDBPyConnection, cutoff: date):
    keys = {
        "opportunity": ["opportunity_id"],
        "opportunity_overlap": ["opportunity_id_a", "opportunity_id_b", "overlap_id"],
        "opportunity_suppression": ["reason"],
        "management_insight": ["insight_id"],
    }
    objects = {}
    for table, key in keys.items():
        columns = [
            {"name": x[1], "type": x[2], "nullable": not x[3]}
            for x in con.execute(f"PRAGMA table_info('{table}')").fetchall()
        ]
        if {x["name"] for x in columns} & PRIVATE_LABEL_NAMES:
            raise OpportunityContractError("private label in output schema")
        digest = hashlib.sha256()
        order = ",".join(f'"{column}"' for column in key)
        result = con.execute(f'SELECT * FROM "{table}" ORDER BY {order}')
        while batch := result.fetchmany(1000):
            for row in batch:
                canonical = json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":"))
                digest.update(canonical.encode("utf-8") + b"\n")
        objects[table] = {
            "schema_id": f"bsi.opportunity_insights.{table}",
            "schema_version": 1,
            "object_type": "table",
            "grain": "+".join(key),
            "key": key,
            "row_count": con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
            "content_sha256": digest.hexdigest(),
            "columns": columns,
            "as_of_date": str(cutoff),
            "currency": "EUR for monetary values; scores 0-100; ratios unitless",
        }
    return objects


def build_opportunity_insights(
    configuration: ProjectConfiguration,
    policy: OpportunityPolicy,
    data_root: Path,
    output_directory: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> OpportunityBuildResult:
    """Read-only upstream inputs, staged database, atomic publication."""
    data_root, output_directory = Path(data_root), Path(output_directory)
    sources = validate_sources(configuration, data_root)
    if publish and _has_material_output(output_directory) and not force:
        raise OpportunityContractError(
            f"opportunity output exists; use --force: {output_directory}"
        )
    if publish:
        output_directory.parent.mkdir(parents=True, exist_ok=True)
    cutoff = configuration.application.reporting.default_as_of_date
    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-",
        dir=output_directory.parent if publish else None,
    ) as temporary:
        staged = Path(temporary) / output_directory.name
        staged.mkdir()
        (staged / ".gitkeep").touch()
        db = staged / DATABASE_NAME
        try:
            con = duckdb.connect(str(db))
        except duckdb.Error as exc:
            raise OpportunityContractError(
                f"cannot create staged opportunity database: {exc}"
            ) from exc
        try:
            con.execute("PRAGMA threads=1")
            for label, (_, _, source_db) in sources.items():
                path = str(source_db.resolve()).replace("'", "''")
                con.execute(f"ATTACH '{path}' AS {label} (READ_ONLY)")
            _validate_finance(con, cutoff)
            facts = load_facts(con, cutoff)
            opportunities, overlaps, suppression = build_opportunities(facts, policy, cutoff)
            insights = evaluate_insights(facts, policy, cutoff)
            _create_tables(con)
            _insert(con, "opportunity", OPPORTUNITY_KEYS, opportunities)
            _insert(
                con,
                "opportunity_overlap",
                ("opportunity_id_a", "opportunity_id_b", "overlap_id", "must_not_sum"),
                [
                    dict(opportunity_id_a=a, opportunity_id_b=b, overlap_id=i, must_not_sum=True)
                    for a, b, i in overlaps
                ],
            )
            _insert(
                con,
                "opportunity_suppression",
                ("reason", "suppressed_count"),
                [dict(reason=k, suppressed_count=v) for k, v in sorted(suppression.items())],
            )
            _insert(con, "management_insight", INSIGHT_KEYS, insights)
            validations = {
                "financial_reconciliation": {"status": "passed"},
                "output": _validate_result(con, cutoff),
            }
            objects = _objects(con, cutoff)
            for label in reversed(tuple(sources)):
                con.execute(f"DETACH {label}")
            con.execute("CHECKPOINT")
        except duckdb.Error as exc:
            raise OpportunityContractError(f"opportunity SQL failed: {exc}") from exc
        except (TypeError, KeyError, ArithmeticError) as exc:
            raise OpportunityContractError(
                f"opportunity input or calculation failed: {exc}"
            ) from exc
        finally:
            con.close()
        manifests = {label: x[1] for label, x in sources.items()}
        databases = {label: x[0]["database"]["sha256"] for label, x in sources.items()}
        manifest = {
            "schema_id": "bsi.opportunity_insights_publication",
            "schema_version": 1,
            "stage": "insights",
            "publication_status": "passed",
            "pipeline_version": __version__,
            "source_batch_id": sources["warehouse"][0]["source_batch_id"],
            "build_config_hash": configuration.deterministic_hash(),
            "opportunity_policy_hash": policy.deterministic_hash(),
            "source_manifests_sha256": manifests,
            "source_databases_sha256": databases,
            "source_silver_manifest_sha256": sources["warehouse"][0][
                "source_silver_manifest_sha256"
            ],
            "database": {"path": DATABASE_NAME, "sha256": _sha(db)},
            "objects": objects,
            "analysis_window": {
                "period_start": "2022-01-01",
                "as_of_date": str(cutoff),
                "post_cutoff_excluded": True,
            },
            "methods": {
                "opportunity": policy.method_version,
                "scoring_weights": policy.score_weights.model_dump(),
                "financial_basis": "EUR net gross profit before freight; illustrative 12-month scenarios",
                "return_rate": "not_estimable: no approved mature cohort duration",
                "non_additivity": "overlap relationships require deduplication",
            },
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    name: importlib.metadata.version(name) for name in ("duckdb", "pydantic")
                },
            },
            "validations": validations,
        }
        (staged / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if not publish:
            return OpportunityBuildResult(manifest, validations)
        _publish_directory(staged, output_directory, force)
    return OpportunityBuildResult(
        manifest, validations, output_directory, output_directory / DATABASE_NAME
    )
