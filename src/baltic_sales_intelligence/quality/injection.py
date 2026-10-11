"""Reproducible raw copies with deliberately planted data-quality defects."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import DefectInjectionSettings, ProjectConfiguration
from baltic_sales_intelligence.generation.generator import canonical_content_hash
from baltic_sales_intelligence.generation.reference_data import PUBLIC_COLUMNS

REGISTRY_COLUMNS = (
    "injection_instance_id",
    "injection_seed",
    "seed_component",
    "dataset",
    "defect_id",
    "variant",
    "operation",
    "target_source_row",
    "raw_row_number",
    "target_business_row_id",
    "target_field",
    "original_value",
    "corrupted_value",
    "expected_downstream_action",
    "dependency_impact",
)

PROVENANCE_COLUMNS = (
    "dataset",
    "raw_row_number",
    "source_file",
    "source_row",
    "source_business_row_id",
    "record_instance",
    "original_row_sha256",
)


@dataclass(frozen=True)
class InjectionResult:
    """In-memory raw batch and its deterministic audit artifacts."""

    tables: dict[str, pd.DataFrame]
    registry: pd.DataFrame
    provenance: pd.DataFrame
    counts: pd.DataFrame
    manifest: dict[str, Any]
    output_directory: Path | None = None
    registry_directory: Path | None = None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value


def _encoded_value(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, separators=(",", ":"))


def _csv_value(value: Any) -> str:
    value = _json_value(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    serialised = pd.DataFrame(
        {
            column: frame[column].map(_csv_value)
            for column in frame.columns
        }
    )
    serialised.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def _row_hash(row: tuple[Any, ...]) -> str:
    payload = "\x1f".join(_csv_value(value) for value in row).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_validated_truth(
    truth_directory: str | Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], str]:
    """Validate the published truth manifest and load its canonical Parquet datasets."""

    source = Path(truth_directory)
    manifest_path = source / "generation_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"canonical truth manifest is missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("stage") != "generate" or manifest.get("quality_status") != "passed":
        raise ValueError("canonical truth manifest is not an approved successful generation")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, dict) or set(datasets) != set(PUBLIC_COLUMNS):
        raise ValueError("canonical truth manifest dataset inventory does not match the contract")

    tables: dict[str, pd.DataFrame] = {}
    for name in PUBLIC_COLUMNS:
        metadata = datasets[name]
        for format_name in ("csv", "parquet"):
            relative = metadata.get(f"{format_name}_path")
            expected = metadata.get(f"{format_name}_sha256")
            path = source / str(relative)
            if not path.is_file() or not expected or _file_hash(path) != expected:
                raise ValueError(f"canonical truth checksum mismatch: {name}.{format_name}")
        frame = pd.read_parquet(source / metadata["parquet_path"])
        if tuple(frame.columns) != PUBLIC_COLUMNS[name]:
            raise ValueError(f"canonical truth schema mismatch: {name}")
        if len(frame) != int(metadata["row_count"]):
            raise ValueError(f"canonical truth row-count mismatch: {name}")
        if name == "returns":
            frame["accepted_quantity"] = pd.Series(
                [
                    None if pd.isna(value) else int(value)
                    for value in frame["accepted_quantity"]
                ],
                dtype=object,
            )
        if canonical_content_hash(frame) != metadata["canonical_sha256"]:
            raise ValueError(f"canonical truth logical checksum mismatch: {name}")
        tables[name] = frame
    return tables, manifest, hashlib.sha256(manifest_bytes).hexdigest()


class _Injector:
    def __init__(self, configuration: ProjectConfiguration, tables: dict[str, pd.DataFrame]):
        self.configuration = configuration
        self.tables = {name: frame.copy(deep=True) for name, frame in tables.items()}
        self.originals = {name: frame.copy(deep=True) for name, frame in tables.items()}
        self.provenance: dict[str, list[dict[str, Any]]] = {}
        self.registry: list[dict[str, Any]] = []
        self.instance_counts = {code: 0 for code in configuration.data_quality.defect_codes}
        for name, frame in self.tables.items():
            source_file = f"{name}.csv"
            self.provenance[name] = [
                {
                    "dataset": name,
                    "source_file": source_file,
                    "source_row": position + 2,
                    "source_business_row_id": _csv_value(row[0]),
                    "record_instance": 1,
                    "original_row_sha256": _row_hash(row),
                }
                for position, row in enumerate(frame.itertuples(index=False, name=None))
            ]

    def _rule(self, code: str) -> DefectInjectionSettings:
        return self.configuration.data_quality.defects[code]

    def _rng(self, code: str, dataset: str) -> np.random.Generator:
        rule = self._rule(code)
        dataset_component = tuple(PUBLIC_COLUMNS).index(dataset) + 1
        sequence = np.random.SeedSequence(
            self.configuration.data_quality.injection.master_seed,
            spawn_key=(rule.seed_component, dataset_component),
        )
        return np.random.Generator(np.random.PCG64(sequence))

    def _count(self, code: str, eligible_count: int, minimum: int = 1) -> int:
        if eligible_count <= 0:
            return 0
        override = self.configuration.data_quality.injection.test_mode_overrides.get(code)
        if override is not None:
            return min(eligible_count, override)
        requested = round(eligible_count * self._rule(code).fraction)
        return min(eligible_count, max(minimum, requested))

    def _positions(
        self, code: str, dataset: str, eligible: list[int] | None = None, minimum: int = 1
    ) -> list[int]:
        positions = (
            eligible
            if eligible is not None
            else list(range(len(self.originals[dataset])))
        )
        if not positions:
            return []
        rng = self._rng(code, dataset)
        selected = rng.choice(
            np.array(positions, dtype=int),
            size=self._count(code, len(positions), minimum),
            replace=False,
        )
        return [int(value) for value in np.atleast_1d(selected)]

    def _record(
        self,
        code: str,
        dataset: str,
        position: int,
        field: str,
        original: Any,
        corrupted: Any,
        action: str,
        *,
        variant: str,
        operation: str = "mutation",
        impact: str = "row only",
        source_position: int | None = None,
    ) -> None:
        self.instance_counts[code] += 1
        provenance = self.provenance[dataset][
            position if source_position is None else source_position
        ]
        self.registry.append(
            {
                "injection_instance_id": (
                    f"INJ-{code}-{self.instance_counts[code]:06d}"
                ),
                "injection_seed": self.configuration.data_quality.injection.master_seed,
                "seed_component": self._rule(code).seed_component,
                "dataset": dataset,
                "defect_id": code,
                "variant": variant,
                "operation": operation,
                "target_source_row": provenance["source_row"],
                "raw_row_number": position + 2,
                "target_business_row_id": _csv_value(self.tables[dataset].iloc[position, 0]),
                "target_field": field,
                "original_value": _encoded_value(original),
                "corrupted_value": _encoded_value(corrupted),
                "expected_downstream_action": action,
                "dependency_impact": impact,
            }
        )

    def _mutate(
        self,
        code: str,
        dataset: str,
        position: int,
        field: str,
        corrupted: Any,
        action: str,
        *,
        variant: str,
        impact: str = "row only",
    ) -> None:
        original = self.tables[dataset].iloc[position][field]
        self.tables[dataset].at[position, field] = corrupted
        self._record(
            code,
            dataset,
            position,
            field,
            original,
            corrupted,
            action,
            variant=variant,
            impact=impact,
        )

    def _append(
        self,
        code: str,
        dataset: str,
        source_position: int,
        row: pd.Series,
        field: str,
        original: Any,
        corrupted: Any,
        action: str,
        *,
        variant: str,
        operation: str,
        impact: str = "duplicate group",
    ) -> int:
        position = len(self.tables[dataset])
        self.tables[dataset] = pd.concat(
            [self.tables[dataset], row.to_frame().T], ignore_index=True
        )
        source = dict(self.provenance[dataset][source_position])
        source["record_instance"] = 1 + sum(
            item["source_row"] == source["source_row"] for item in self.provenance[dataset]
        )
        self.provenance[dataset].append(source)
        self._record(
            code,
            dataset,
            position,
            field,
            original,
            corrupted,
            action,
            variant=variant,
            operation=operation,
            impact=impact,
            source_position=position,
        )
        return position

    def dq01(self) -> None:
        code, dataset = "DQ01", "order_lines"
        for position in self._positions(code, dataset):
            row = self.tables[dataset].iloc[position].copy()
            self._append(
                code, dataset, position, row, "*", row.to_dict(), row.to_dict(),
                "discard exact duplicate and audit", variant="exact_record",
                operation="duplicate_insert",
            )

    def dq02(self) -> None:
        code, dataset = "DQ02", "orders"
        for position in self._positions(code, dataset):
            row = self.tables[dataset].iloc[position].copy()
            original = row["payment_terms_code"]
            row["payment_terms_code"] = "net_45" if original != "net_45" else "net_14"
            self._append(
                code, dataset, position, row, "payment_terms_code", original,
                row["payment_terms_code"], "quarantine conflicting key group",
                variant="conflicting_business_key", operation="conflicting_duplicate_insert",
            )

    def dq03(self) -> None:
        aliases = {"LT": "Lithuania", "LV": "Latvija", "EE": "Estonia"}
        dataset = "customer_attribute_history"
        for position in self._positions("DQ03", dataset):
            original = self.tables[dataset].iloc[position]["home_country_code"]
            self._mutate(
                "DQ03", dataset, position, "home_country_code", aliases[str(original)],
                "repair documented country alias", variant="country_alias",
            )

    def dq04(self) -> None:
        dataset = "orders"
        eligible = [
            index for index, value in enumerate(self.originals[dataset]["payment_terms_code"])
            if not pd.isna(value)
        ]
        for position in self._positions("DQ04", dataset, eligible):
            original = self.tables[dataset].iloc[position]["payment_terms_code"]
            self._mutate(
                "DQ04", dataset, position, "payment_terms_code", f" {str(original).upper()} ",
                "trim and normalise approved controlled code", variant="case_whitespace",
            )

    def dq05(self) -> None:
        dataset = "customer_attribute_history"
        city_region_counts = self.originals["cities"].groupby("city_id")[
            "region_id"
        ].nunique()
        uniquely_mapped_cities = set(city_region_counts[city_region_counts == 1].index)
        eligible = [
            index
            for index, city_id in enumerate(self.originals[dataset]["home_city_id"])
            if city_id in uniquely_mapped_cities
        ]
        for position in self._positions("DQ05", dataset, eligible):
            self._mutate(
                "DQ05", dataset, position, "home_region_id", None,
                "repair from unique city-region crosswalk", variant="missing_region",
            )

    def dq06(self) -> None:
        dataset = "orders"
        for position in self._positions("DQ06", dataset):
            self._mutate(
                "DQ06", dataset, position, "order_date", "2025-13-40",
                "quarantine malformed date", variant="malformed_date",
            )

    def dq07(self) -> None:
        dataset = "order_lines"
        for number, position in enumerate(self._positions("DQ07", dataset, minimum=2)):
            value = 0 if number % 2 == 0 else -1
            self._mutate(
                "DQ07", dataset, position, "quantity_ordered", value,
                "quarantine nonpositive sold quantity",
                variant="zero" if value == 0 else "negative",
            )

    def dq08(self) -> None:
        dataset = "order_lines"
        for number, position in enumerate(self._positions("DQ08", dataset, minimum=2)):
            value = Decimal("1.250000") if number % 2 == 0 else Decimal("-0.050000")
            self._mutate(
                "DQ08", dataset, position, "discount_rate", value,
                "quarantine discount outside zero-to-one range",
                variant="above_one" if value > 1 else "negative",
            )

    def dq09(self) -> None:
        for position in self._positions("DQ09", "product_subcategories"):
            self._mutate(
                "DQ09", "product_subcategories", position, "category_id", None,
                "repair only from canonical subcategory-category crosswalk",
                variant="missing_nonhistorical_category",
            )
        for position in self._positions("DQ09", "order_lines"):
            self._mutate(
                "DQ09", "order_lines", position, "unit_cost_eur", None,
                "quarantine; never backfill historical cost", variant="missing_historical_cost",
            )

    def dq10(self) -> None:
        dataset = "orders"
        eligible_count = len(self.originals[dataset])
        total = self._count("DQ10", eligible_count, minimum=2)
        rng = self._rng("DQ10", dataset)
        salesperson_count = total // 2
        customer_count = total - salesperson_count
        salesperson_eligible = [
            index
            for index, value in enumerate(
                self.originals[dataset]["credited_salesperson_id"]
            )
            if not pd.isna(value)
        ]
        salesperson_count = min(salesperson_count, len(salesperson_eligible))
        salesperson_positions = [
            int(value)
            for value in np.atleast_1d(
                rng.choice(salesperson_eligible, size=salesperson_count, replace=False)
            )
        ]
        customer_eligible = [
            position
            for position in range(eligible_count)
            if position not in set(salesperson_positions)
        ]
        customer_count = min(
            len(customer_eligible), customer_count + total // 2 - salesperson_count
        )
        customer_positions = [
            int(value)
            for value in np.atleast_1d(
                rng.choice(customer_eligible, size=customer_count, replace=False)
            )
        ]
        variants = (
            ("customer_id", "ORPHAN-CUSTOMER", "orphan_customer", "orders and dependent lines"),
            (
                "credited_salesperson_id",
                "ORPHAN-SALESPERSON",
                "orphan_salesperson",
                "order attribution",
            ),
        )
        for positions, (field, value, variant, impact) in zip(
            (customer_positions, salesperson_positions), variants, strict=True
        ):
            for position in positions:
                self._mutate(
                    "DQ10", dataset, position, field, value,
                    "quarantine affected dependent row and expose cascade",
                    variant=variant, impact=impact,
                )
        for position in self._positions("DQ10", "order_lines"):
            self._mutate(
                "DQ10", "order_lines", position, "product_id", "ORPHAN-PRODUCT",
                "quarantine affected dependent row and expose cascade",
                variant="orphan_product", impact="line and return chain",
            )

    def dq11(self) -> None:
        dataset = "order_lines"
        for position in self._positions("DQ11", dataset):
            self._mutate(
                "DQ11", dataset, position, "posted_net_sales_eur", "EUR ??",
                "quarantine malformed monetary amount", variant="malformed_text_amount",
            )

    def dq12(self) -> None:
        dataset = "returns"
        accepted = [
            int(index)
            for index in self.originals[dataset]
            .query("return_status == 'accepted'")
            .drop_duplicates("order_line_id")
            .index
        ]
        positions = self._positions("DQ12", dataset, accepted, minimum=2)
        for number, source_position in enumerate(positions):
            if number % 2 == 0:
                self._mutate(
                    "DQ12", dataset, source_position, "accepted_quantity", 0,
                    "quarantine invalid accepted quantity",
                    variant="nonpositive_accepted_quantity",
                )
                continue
            row = self.tables[dataset].iloc[source_position].copy()
            line_id = row["order_line_id"]
            sold = int(
                self.originals["order_lines"]
                .set_index("order_line_id")
                .loc[line_id, "quantity_ordered"]
            )
            original = row.to_dict()
            row["return_id"] = f"RET-DQ12-{self.instance_counts['DQ12'] + 1:06d}"
            row["requested_quantity"] = sold
            row["accepted_quantity"] = sold
            line_returns = self.originals[dataset][
                self.originals[dataset]["order_line_id"] == line_id
            ]
            decisions = line_returns["return_decision_date"].dropna().tolist()
            decisions.extend(line_returns["return_requested_date"].dropna().tolist())
            row["return_decision_date"] = max(decisions) + timedelta(days=1)
            self._append(
                "DQ12", dataset, source_position, row, "accepted_quantity", original,
                row.to_dict(), "quarantine later accepted event exceeding cumulative cap",
                variant="cumulative_cap_violation", operation="dependent_event_insert",
                impact="accepted-return cumulative ordering",
            )

    def dq13(self) -> None:
        variants = (
            (
                "customer_attribute_history", "customer_version_id", "CVER-DQ13-",
                "overlapping_customer_scd",
            ),
            (
                "customer_account_assignments", "assignment_id", "ASG-DQ13-",
                "overlapping_account_ownership",
            ),
            (
                "product_supplier_relationships",
                "product_supplier_relationship_id",
                "PSR-DQ13-",
                "overlapping_supplier_interval",
            ),
        )
        for dataset, key, prefix, variant in variants:
            for position in self._positions("DQ13", dataset):
                row = self.tables[dataset].iloc[position].copy()
                original = row.to_dict()
                row[key] = f"{prefix}{self.instance_counts['DQ13'] + 1:06d}"
                self._append(
                    "DQ13", dataset, position, row, "valid_from", original, row.to_dict(),
                    "quarantine interval conflict and preserve both records",
                    variant=variant, operation="overlapping_interval_insert",
                )

    def dq14(self) -> None:
        dataset = "sales_targets"
        positions = self._positions("DQ14", dataset, minimum=2)
        for number, position in enumerate(positions):
            row = self.tables[dataset].iloc[position].copy()
            if number % 2 == 0:
                self._append(
                    "DQ14", dataset, position, row, "*", row.to_dict(), row.to_dict(),
                    "discard exact duplicate and audit", variant="exact_target_duplicate",
                    operation="duplicate_insert",
                )
                continue
            original = row["target_value"]
            row["target_value"] = Decimal(str(original)) + Decimal("1.00")
            self._append(
                "DQ14", dataset, position, row, "target_value", original, row["target_value"],
                "quarantine conflicting target allocation",
                variant="conflicting_target_duplicate", operation="conflicting_duplicate_insert",
            )

    def run(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
        execution_order = (*self.configuration.data_quality.defect_codes[1:], "DQ01")
        for code in execution_order:
            rule = self._rule(code)
            if rule.enabled:
                getattr(self, code.lower())()
        registry = pd.DataFrame(self.registry, columns=REGISTRY_COLUMNS)
        provenance_rows = []
        for records in self.provenance.values():
            for position, record in enumerate(records):
                provenance_rows.append(
                    {"raw_row_number": position + 2, **record}
                )
        provenance = pd.DataFrame(provenance_rows, columns=PROVENANCE_COLUMNS)
        return self.tables, registry, provenance


def _build_counts(registry: pd.DataFrame) -> pd.DataFrame:
    if registry.empty:
        return pd.DataFrame(columns=("dataset", "defect_id", "instance_count"))
    return (
        registry.groupby(["dataset", "defect_id"], sort=True)
        .size()
        .rename("instance_count")
        .reset_index()
    )


def inject_defects(
    configuration: ProjectConfiguration,
    tables: dict[str, pd.DataFrame],
    *,
    source_manifest_sha256: str = "in_memory_fixture",
) -> InjectionResult:
    """Create deterministic corrupted raw copies without mutating canonical inputs."""

    if not configuration.data_quality.injection.enabled:
        raise ValueError("defect injection is disabled by configuration")
    missing = set(PUBLIC_COLUMNS).difference(tables)
    if missing:
        raise ValueError(f"canonical input tables are missing: {sorted(missing)}")
    source_hashes = {name: canonical_content_hash(tables[name]) for name in PUBLIC_COLUMNS}
    raw, registry, provenance = _Injector(configuration, tables).run()
    if source_hashes != {name: canonical_content_hash(tables[name]) for name in PUBLIC_COLUMNS}:
        raise RuntimeError("canonical truth was mutated during defect injection")
    counts = _build_counts(registry)
    config_payload = json.dumps(
        configuration.data_quality.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    config_hash = hashlib.sha256(config_payload.encode()).hexdigest()
    batch_id = hashlib.sha256(
        f"{source_manifest_sha256}:{config_hash}".encode()
    ).hexdigest()[:16]
    manifest = {
        "schema_id": "bsi.raw_defect_injection",
        "schema_version": 1,
        "stage": "inject-defects",
        "generator_version": __version__,
        "batch_id": batch_id,
        "quality_status": "injected_not_validated",
        "source_manifest_sha256": source_manifest_sha256,
        "config_hash": config_hash,
        "master_injection_seed": configuration.data_quality.injection.master_seed,
        "stage_duration_seconds": 0.0,
        "package_versions": {
            name: importlib.metadata.version(name) for name in ("numpy", "pandas", "pyarrow")
        },
        "datasets": {
            name: {
                "schema_id": f"bsi.raw.{name}",
                "schema_version": 1,
                "source_row_count": len(tables[name]),
                "raw_row_count": len(frame),
                "columns": list(frame.columns),
                "path": f"{name}.csv",
            }
            for name, frame in raw.items()
        },
        "artifacts": {
            "source_provenance": "source_provenance.csv",
            "defect_counts": "defect_counts.csv",
            "private_registry": "../qa/injections/private_injection_registry.csv",
        },
        "defect_counts": {
            code: int((registry["defect_id"] == code).sum())
            for code in configuration.data_quality.defect_codes
        },
    }
    return InjectionResult(raw, registry, provenance, counts, manifest)


def publish_raw_injection(
    configuration: ProjectConfiguration,
    truth_directory: str | Path,
    output_directory: str | Path,
    registry_directory: str | Path,
    *,
    publish: bool = True,
) -> InjectionResult:
    """Validate canonical truth, inject defects, and atomically publish completed artifacts."""

    tables, _source_manifest, source_manifest_hash = load_validated_truth(truth_directory)
    result = inject_defects(
        configuration, tables, source_manifest_sha256=source_manifest_hash
    )
    if not publish:
        return result

    output = Path(output_directory)
    registry_output = Path(registry_directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    registry_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".injection-", dir=output.parent) as temporary:
        staging_root = Path(temporary)
        raw_staging = staging_root / "raw"
        qa_staging = staging_root / "qa"
        raw_staging.mkdir()
        qa_staging.mkdir()
        manifest = json.loads(json.dumps(result.manifest))
        for name, frame in result.tables.items():
            path = raw_staging / f"{name}.csv"
            _write_csv(frame, path)
            manifest["datasets"][name]["sha256"] = _file_hash(path)
        provenance_path = raw_staging / "source_provenance.csv"
        counts_path = raw_staging / "defect_counts.csv"
        registry_path = qa_staging / "private_injection_registry.csv"
        _write_csv(result.provenance, provenance_path)
        _write_csv(result.counts, counts_path)
        _write_csv(result.registry, registry_path)
        manifest["artifacts"].update(
            {
                "source_provenance_sha256": _file_hash(provenance_path),
                "defect_counts_sha256": _file_hash(counts_path),
                "private_registry_sha256": _file_hash(registry_path),
            }
        )
        manifest_path = raw_staging / "raw_injection_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        raw_backup = staging_root / "previous_raw"
        registry_backup = staging_root / "previous_registry"
        raw_published = False
        registry_published = False
        try:
            if output.exists():
                os.replace(output, raw_backup)
            os.replace(raw_staging, output)
            raw_published = True
            if registry_output.exists():
                os.replace(registry_output, registry_backup)
            os.replace(qa_staging, registry_output)
            registry_published = True
        except OSError:
            if registry_published:
                os.replace(registry_output, qa_staging)
            if registry_backup.exists():
                os.replace(registry_backup, registry_output)
            if raw_published:
                os.replace(output, raw_staging)
            if raw_backup.exists():
                os.replace(raw_backup, output)
            raise
    return InjectionResult(
        result.tables,
        result.registry,
        result.provenance,
        result.counts,
        manifest,
        output,
        registry_output,
    )
