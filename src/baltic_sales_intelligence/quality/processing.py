"""Deterministic RAW -> validate/repair/quarantine -> Silver processing."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd
import pandera.errors
import pyarrow as pa
import pyarrow.parquet as pq

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.financial import (
    ZERO,
    allocate_freight,
    quantize_unit,
    return_postings,
    sale_postings,
)
from baltic_sales_intelligence.generation.reference_data import (
    PRIVATE_LABEL_NAMES,
    PRIVATE_REGISTRY_FIELDS,
    PUBLIC_COLUMNS,
)
from baltic_sales_intelligence.quality.contracts import (
    BOOLEAN_COLUMNS,
    DATE_COLUMNS,
    DECIMAL_COLUMNS,
    ENUMS,
    FOREIGN_KEYS,
    INTEGER_COLUMNS,
    MONEY_DECIMAL_COLUMNS,
    OPTIONAL_COLUMNS,
    PRIMARY_KEYS,
    UNIT_DECIMAL_COLUMNS,
    arrow_schema,
    decimal_quantum,
    raw_schema,
    silver_pandera_schema,
)

_DATE_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")
_INTEGER_PATTERN = re.compile(r"\A-?\d+\Z")
_DECIMAL_PATTERN = re.compile(r"\A[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_COUNTRY_ALIASES = {
    "lithuania": "LT",
    "lietuva": "LT",
    "latvia": "LV",
    "latvija": "LV",
    "estonia": "EE",
    "eesti": "EE",
}
_STATUS_RANK = {
    "accepted_unchanged": 0,
    "repaired": 1,
    "discarded_exact_duplicate": 2,
    "quarantined": 3,
}


class DataQualityError(ValueError):
    """Raised when a raw batch or prospective Silver publication fails closed."""


@dataclass
class DatasetState:
    name: str
    raw: pd.DataFrame
    provenance: pd.DataFrame
    values: pd.DataFrame
    status: list[str]
    violations: list[list[str]]
    reasons: list[list[str]]
    dependency_context: list[list[str]]


@dataclass(frozen=True)
class QualityResult:
    """Validated in-memory outputs and deterministic audit artifacts."""

    silver: dict[str, pd.DataFrame]
    quarantine: dict[str, pd.DataFrame]
    decisions: pd.DataFrame
    dispositions: pd.DataFrame
    manifest: dict[str, Any]
    raw_manifest_sha256: str
    output_directory: Path | None = None
    quarantine_directory: Path | None = None


def accepted_return_cap_violations(
    returns: pd.DataFrame, sold_quantities: dict[str, int]
) -> list[Any]:
    """Return indexes of later accepted events that exceed an order-line quantity cap."""

    offending: list[Any] = []
    accepted = returns.loc[returns["return_status"] == "accepted"]
    for line_id, events in accepted.groupby("order_line_id", sort=True):
        cumulative = 0
        sold = sold_quantities[line_id]
        ordered = events.sort_values(["return_decision_date", "return_id"], kind="stable")
        for index, event in ordered.iterrows():
            quantity = int(event["accepted_quantity"])
            if cumulative + quantity > sold:
                offending.append(index)
            else:
                cumulative += quantity
    return offending


def _financial_children_by_order(
    lines: pd.DataFrame,
) -> dict[Any, tuple[list[Decimal], list[int]]]:
    """Collect freight inputs by order while preserving active line order."""

    children: dict[Any, tuple[list[Decimal], list[int]]] = {}
    values = zip(
        lines["order_id"],
        lines["posted_net_sales_eur"],
        lines["quantity_ordered"],
        strict=True,
    )
    for order_id, net_sales, quantity in values:
        freight_inputs = children.get(order_id)
        if freight_inputs is None:
            freight_inputs = ([], [])
            children[order_id] = freight_inputs
        freight_inputs[0].append(net_sales)
        freight_inputs[1].append(quantity)
    return children


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (date, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dq_config_hash(configuration: ProjectConfiguration) -> str:
    payload = json.dumps(
        configuration.data_quality.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_raw_batch(
    configuration: ProjectConfiguration, raw_directory: Path
) -> tuple[dict[str, DatasetState], dict[str, Any], str]:
    manifest_path = raw_directory / "raw_injection_manifest.json"
    if not manifest_path.is_file():
        raise DataQualityError(f"raw manifest is missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise DataQualityError(f"raw manifest is not valid JSON: {exc}") from exc
    if (
        manifest.get("schema_id") != "bsi.raw_defect_injection"
        or manifest.get("schema_version") != 1
        or manifest.get("stage") != "inject-defects"
        or manifest.get("quality_status") != "injected_not_validated"
    ):
        raise DataQualityError("raw manifest stage, schema version, or status is incompatible")
    if manifest.get("config_hash") != _dq_config_hash(configuration):
        raise DataQualityError("raw batch data-quality configuration hash is incompatible")
    if manifest.get("generator_version") != __version__:
        raise DataQualityError("raw batch generator version is incompatible")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, dict) or set(datasets) != set(PUBLIC_COLUMNS):
        raise DataQualityError("raw manifest dataset inventory does not match the contract")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise DataQualityError("raw manifest artifact inventory is missing")
    # The private_registry entry is deliberately never resolved or read here.
    for artifact in ("source_provenance", "defect_counts"):
        relative = artifacts.get(artifact)
        expected = artifacts.get(f"{artifact}_sha256")
        path = raw_directory / str(relative)
        if not path.is_file() or not expected or _file_hash(path) != expected:
            raise DataQualityError(f"raw artifact checksum mismatch: {artifact}")

    provenance_path = raw_directory / str(artifacts["source_provenance"])
    provenance = pd.read_csv(provenance_path, dtype=str, keep_default_na=False)
    required_provenance = {
        "dataset",
        "raw_row_number",
        "source_file",
        "source_row",
        "source_business_row_id",
        "record_instance",
        "original_row_sha256",
    }
    if set(provenance.columns) != required_provenance:
        raise DataQualityError("source provenance columns do not match the contract")
    if provenance.duplicated(["dataset", "raw_row_number"]).any():
        raise DataQualityError("source provenance contains duplicate raw row coordinates")

    states: dict[str, DatasetState] = {}
    private_names = PRIVATE_LABEL_NAMES | PRIVATE_REGISTRY_FIELDS
    for name, columns in PUBLIC_COLUMNS.items():
        metadata = datasets[name]
        if (
            metadata.get("schema_id") != f"bsi.raw.{name}"
            or metadata.get("schema_version") != 1
            or tuple(metadata.get("columns", ())) != columns
        ):
            raise DataQualityError(f"raw schema declaration mismatch: {name}")
        relative = metadata.get("path")
        path = raw_directory / str(relative)
        if not path.is_file():
            raise DataQualityError(f"raw dataset is missing: {path}")
        if not metadata.get("sha256") or _file_hash(path) != metadata["sha256"]:
            raise DataQualityError(f"raw dataset checksum mismatch: {name}")
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if tuple(frame.columns) != columns:
            leaked = sorted(set(frame.columns) & private_names)
            suffix = f"; private columns rejected: {leaked}" if leaked else ""
            raise DataQualityError(f"raw dataset columns do not match {name}{suffix}")
        if len(frame) != int(metadata.get("raw_row_count", -1)):
            raise DataQualityError(f"raw row-count mismatch: {name}")
        try:
            raw_schema(name).validate(frame, lazy=True)
        except pandera.errors.SchemaErrors as exc:
            raise DataQualityError(f"raw Pandera schema validation failed: {name}") from exc
        dataset_provenance = provenance.loc[provenance["dataset"] == name].copy()
        dataset_provenance["raw_row_number_int"] = dataset_provenance["raw_row_number"].map(
            lambda value: int(value) if _INTEGER_PATTERN.fullmatch(value) else -1
        )
        dataset_provenance = dataset_provenance.sort_values("raw_row_number_int")
        expected_rows = list(range(2, len(frame) + 2))
        if dataset_provenance["raw_row_number_int"].tolist() != expected_rows:
            raise DataQualityError(f"source provenance coverage mismatch: {name}")
        if set(dataset_provenance["source_file"]) != {str(relative)}:
            raise DataQualityError(f"source provenance filename mismatch: {name}")
        if dataset_provenance.duplicated(["source_row", "record_instance"]).any():
            raise DataQualityError(f"source provenance record instances are not unique: {name}")
        source_instances = dataset_provenance["record_instance"].map(
            lambda value: int(value) if _INTEGER_PATTERN.fullmatch(value) else -1
        )
        if (
            (source_instances < 1).any()
            or int((source_instances == 1).sum())
            != int(metadata.get("source_row_count", -1))
        ):
            raise DataQualityError(f"source provenance count mismatch: {name}")
        dataset_provenance = dataset_provenance.reset_index(drop=True)
        states[name] = DatasetState(
            name=name,
            raw=frame,
            provenance=dataset_provenance,
            values=frame.copy(deep=True),
            status=["accepted_unchanged"] * len(frame),
            violations=[[] for _ in range(len(frame))],
            reasons=[[] for _ in range(len(frame))],
            dependency_context=[[] for _ in range(len(frame))],
        )
    if len(provenance) != sum(len(state.raw) for state in states.values()):
        raise DataQualityError("source provenance contains rows outside the dataset inventory")
    return states, manifest, hashlib.sha256(manifest_bytes).hexdigest()


class _QualityEngine:
    def __init__(
        self,
        configuration: ProjectConfiguration,
        states: dict[str, DatasetState],
        manifest: dict[str, Any],
        raw_manifest_hash: str,
    ) -> None:
        self.configuration = configuration
        self.states = states
        self.raw_manifest = manifest
        self.raw_manifest_hash = raw_manifest_hash
        self.batch_id = str(manifest["batch_id"])
        self.decision_rows: list[dict[str, Any]] = []

    def _decision(
        self,
        dataset: str,
        index: int,
        rule_id: str,
        action: str,
        reason: str,
        *,
        severity: str = "high",
        repair_details: dict[str, Any] | None = None,
        dependency: str | None = None,
    ) -> None:
        state = self.states[dataset]
        if rule_id not in state.violations[index]:
            state.violations[index].append(rule_id)
        if reason not in state.reasons[index]:
            state.reasons[index].append(reason)
        if dependency and dependency not in state.dependency_context[index]:
            state.dependency_context[index].append(dependency)
        next_status = {
            "repair": "repaired",
            "quarantine": "quarantined",
            "discard_exact_duplicate": "discarded_exact_duplicate",
        }[action]
        if _STATUS_RANK[next_status] > _STATUS_RANK[state.status[index]]:
            state.status[index] = next_status
        self.decision_rows.append(
            {
                "batch_id": self.batch_id,
                "dataset": dataset,
                "source_row": int(state.provenance.iloc[index]["raw_row_number_int"]),
                "rule_id": rule_id,
                "severity": severity,
                "action": action,
                "original_hash": _payload_hash(state.raw.iloc[index].to_dict()),
                "output_hash": "",
                "reason": reason,
                "repair_details": json.dumps(
                    _canonical(repair_details or {}),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )

    def _quarantine(
        self,
        dataset: str,
        index: int,
        rule_id: str,
        reason: str,
        *,
        dependency: str | None = None,
    ) -> None:
        self._decision(
            dataset,
            index,
            rule_id,
            "quarantine",
            reason,
            dependency=dependency,
        )

    def _quarantine_once(self, dataset: str, index: int, rule_id: str, reason: str) -> None:
        """Record one quarantine decision per row and independently detected rule."""

        if rule_id not in self.states[dataset].violations[index]:
            self._quarantine(dataset, index, rule_id, reason)

    def _repair(
        self, dataset: str, index: int, column: str, value: str, rule_id: str, reason: str
    ) -> None:
        state = self.states[dataset]
        original = state.values.at[index, column]
        if original == value:
            return
        state.values.at[index, column] = value
        self._decision(
            dataset,
            index,
            rule_id,
            "repair",
            reason,
            severity="info",
            repair_details={"field": column, "original": original, "repaired": value},
        )

    def _candidate(self, dataset: str, index: int) -> bool:
        return self.states[dataset].status[index] in {"accepted_unchanged", "repaired"}

    def detect_duplicates(self) -> None:
        for name, state in self.states.items():
            key = PRIMARY_KEYS[name]
            key_groups = state.raw.groupby(key, sort=False, dropna=False).indices
            conflicting: set[int] = set()
            for key_value, positions in key_groups.items():
                indexes = [int(item) for item in positions]
                if len(indexes) < 2:
                    continue
                distinct = state.raw.iloc[indexes].drop_duplicates()
                if len(distinct) > 1:
                    for index in indexes:
                        conflicting.add(index)
                        self._quarantine(
                            name,
                            index,
                            "DQ14" if name == "sales_targets" else "DQ02",
                            f"conflicting duplicate business key {key}={key_value!r}",
                        )
            duplicate_rule = "DQ14" if name == "sales_targets" else "DQ01"
            for index in state.raw.index[state.raw.duplicated(keep="first")]:
                position = int(index)
                if position in conflicting:
                    continue
                self._decision(
                    name,
                    position,
                    duplicate_rule,
                    "discard_exact_duplicate",
                    "extra exactly identical source row",
                    severity="warning",
                )

        targets = self.states["sales_targets"]
        active = [
            index for index in range(len(targets.raw)) if self._candidate("sales_targets", index)
        ]
        allocation_keys: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for index in active:
            row = targets.values.iloc[index]
            allocation = row["allocation_type"]
            if allocation == "salesperson":
                key = (row["target_month"], row["metric_code"], allocation, row["salesperson_id"])
            else:
                key = (row["target_month"], row["metric_code"], allocation, row["country_code"])
            allocation_keys[key].append(index)
        for key, indexes in allocation_keys.items():
            if len(indexes) < 2:
                continue
            comparison_columns = [
                column for column in PUBLIC_COLUMNS["sales_targets"] if column != "target_id"
            ]
            distinct = targets.raw.iloc[indexes][comparison_columns].drop_duplicates()
            if len(distinct) == 1:
                for index in indexes[1:]:
                    self._decision(
                        "sales_targets",
                        index,
                        "DQ14",
                        "discard_exact_duplicate",
                        f"duplicate target allocation at leaf grain {key!r}",
                        severity="warning",
                    )
            else:
                for index in indexes:
                    self._quarantine(
                        "sales_targets",
                        index,
                        "DQ14",
                        f"conflicting target allocation at leaf grain {key!r}",
                    )

    def apply_repairs(self) -> None:
        approved_codes: dict[str, str] = {}
        for values in ENUMS.values():
            approved_codes.update({value.casefold(): value for value in values})
        for state in self.states.values():
            for column in state.values.columns:
                if column.endswith("country_code") or column in {
                    "tax_jurisdiction_code",
                    "home_country_code",
                }:
                    for index, original in enumerate(state.values[column]):
                        if not self._candidate(state.name, index) or not original:
                            continue
                        stripped = original.strip()
                        repaired = _COUNTRY_ALIASES.get(stripped.casefold())
                        if repaired is None and stripped.upper() in {"LT", "LV", "EE"}:
                            repaired = stripped.upper()
                        if repaired and repaired != original:
                            self._repair(
                                state.name,
                                index,
                                column,
                                repaired,
                                "DQ03",
                                "normalised documented Baltic country alias",
                            )
                if column in ENUMS:
                    for index, original in enumerate(state.values[column]):
                        if not self._candidate(state.name, index) or not original:
                            continue
                        repaired = approved_codes.get(original.strip().casefold())
                        if repaired is not None and repaired != original:
                            self._repair(
                                state.name,
                                index,
                                column,
                                repaired,
                                "DQ04",
                                "trimmed and normalised an approved controlled code",
                            )

        cities = self.states["cities"]
        city_regions: dict[str, set[str]] = defaultdict(set)
        for index, row in cities.values.iterrows():
            if self._candidate("cities", int(index)) and row["city_id"] and row["region_id"]:
                city_regions[row["city_id"]].add(row["region_id"])
        history = self.states["customer_attribute_history"]
        for index, row in history.values.iterrows():
            position = int(index)
            if not self._candidate(history.name, position) or row["home_region_id"]:
                continue
            matches = city_regions.get(row["home_city_id"], set())
            if len(matches) == 1:
                self._repair(
                    history.name,
                    position,
                    "home_region_id",
                    next(iter(matches)),
                    "DQ05",
                    "reconstructed region from unambiguous approved city crosswalk",
                )
            else:
                self._quarantine(
                    history.name,
                    position,
                    "DQ05",
                    "missing mandatory region has no unambiguous approved city mapping",
                )

        categories = self.states["product_categories"]
        category_by_name = {
            row["category_name"]: row["category_id"]
            for index, row in categories.values.iterrows()
            if self._candidate("product_categories", int(index))
        }
        taxonomy = self.configuration.generation.catalogue.subcategories
        configured_subcategories = {
            subcategory: category_by_name.get(category_name)
            for category_name, subcategories in taxonomy.items()
            for subcategory in subcategories
        }
        subcategories = self.states["product_subcategories"]
        for index, row in subcategories.values.iterrows():
            position = int(index)
            if not self._candidate(subcategories.name, position) or row["category_id"]:
                continue
            category_id = configured_subcategories.get(row["subcategory_name"])
            if category_id:
                self._repair(
                    subcategories.name,
                    position,
                    "category_id",
                    category_id,
                    "DQ09",
                    "restored nonhistorical category from authorised unambiguous taxonomy",
                )
            else:
                self._quarantine(
                    subcategories.name,
                    position,
                    "DQ09",
                    "missing category has no authorised unambiguous taxonomy mapping",
                )

    @staticmethod
    def _parse_value(column: str, value: str, optional: bool) -> Any:
        if value == "":
            if optional:
                return None
            raise ValueError("mandatory value is missing")
        if column in DATE_COLUMNS:
            if not _DATE_PATTERN.fullmatch(value):
                raise ValueError("date is not canonical YYYY-MM-DD")
            return date.fromisoformat(value)
        if column in INTEGER_COLUMNS:
            if not _INTEGER_PATTERN.fullmatch(value):
                raise ValueError("integer is malformed")
            return int(value)
        if column in BOOLEAN_COLUMNS:
            if value not in {"true", "false"}:
                raise ValueError("boolean is not true or false")
            return value == "true"
        if column in DECIMAL_COLUMNS:
            if not _DECIMAL_PATTERN.fullmatch(value):
                raise ValueError("decimal amount is malformed")
            try:
                parsed = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("decimal amount is malformed") from exc
            if not parsed.is_finite():
                raise ValueError("decimal amount is not finite")
            quantum = decimal_quantum(column)
            quantized = parsed.quantize(quantum)
            if quantized != parsed:
                raise ValueError("decimal exceeds contracted scale")
            return quantized
        return value

    def parse_and_validate_rows(self) -> None:
        for name, state in self.states.items():
            optional = OPTIONAL_COLUMNS.get(name, set())
            parsed = state.values.copy(deep=True).astype(object)
            for index in range(len(state.values)):
                if not self._candidate(name, index):
                    continue
                for column in state.values.columns:
                    raw_value = state.values.at[index, column]
                    try:
                        parsed.at[index, column] = self._parse_value(
                            column, raw_value, column in optional
                        )
                    except (ValueError, InvalidOperation):
                        if column in DATE_COLUMNS:
                            rule = "DQ06"
                        elif column in DECIMAL_COLUMNS:
                            rule = "DQ09" if column == "unit_cost_eur" and not raw_value else "DQ11"
                        elif column in {"quantity_ordered"}:
                            rule = "DQ07"
                        elif column in {"requested_quantity", "accepted_quantity"}:
                            rule = "DQ12"
                        else:
                            rule = "DQ10"
                        self._quarantine(name, index, rule, f"invalid {column}: {raw_value!r}")
                        parsed.at[index, column] = None
            state.values = parsed

        self._validate_enums_and_simple_constraints()

    def _validate_enums_and_simple_constraints(self) -> None:
        for name, state in self.states.items():
            for index, row in state.values.iterrows():
                position = int(index)
                if not self._candidate(name, position):
                    continue
                for column, allowed in ENUMS.items():
                    if (
                        column in row.index
                        and row[column] is not None
                        and row[column] not in allowed
                    ):
                        self._quarantine(
                            name,
                            position,
                            "DQ04",
                            f"unknown controlled code {column}={row[column]!r}",
                        )

        self._validate_geography()
        self._validate_entity_rows()

    def _validate_geography(self) -> None:
        region_map = {
            row["region_id"]: row["country_code"]
            for index, row in self.states["regions"].values.iterrows()
            if self._candidate("regions", int(index))
        }
        city_map = {
            row["city_id"]: (row["region_id"], row["country_code"])
            for index, row in self.states["cities"].values.iterrows()
            if self._candidate("cities", int(index))
        }
        for index, row in self.states["cities"].values.iterrows():
            position = int(index)
            if (
                self._candidate("cities", position)
                and region_map.get(row["region_id"]) != row["country_code"]
            ):
                self._quarantine(
                    "cities", position, "DQ10", "city country conflicts with region parent"
                )
        for index, row in self.states["customer_attribute_history"].values.iterrows():
            position = int(index)
            if not self._candidate("customer_attribute_history", position):
                continue
            if city_map.get(row["home_city_id"]) != (
                row["home_region_id"],
                row["home_country_code"],
            ):
                self._quarantine(
                    "customer_attribute_history",
                    position,
                    "DQ10",
                    "customer geography conflicts with approved city hierarchy",
                )
        for index, row in self.states["salespeople"].values.iterrows():
            position = int(index)
            if (
                self._candidate("salespeople", position)
                and row["primary_region_id"] is not None
                and region_map.get(row["primary_region_id"]) != row["primary_country_code"]
            ):
                self._quarantine(
                    "salespeople", position, "DQ10", "salesperson region-country mismatch"
                )

    def _validate_entity_rows(self) -> None:
        for index, row in self.states["customer_attribute_history"].values.iterrows():
            position = int(index)
            if not self._candidate("customer_attribute_history", position):
                continue
            if (row["valid_to"] is not None and row["valid_from"] >= row["valid_to"]) or (
                row["is_current"] != (row["valid_to"] is None)
            ):
                self._quarantine(
                    "customer_attribute_history",
                    position,
                    "DQ13",
                    "invalid SCD interval or current flag",
                )
        for dataset in ("customer_account_assignments", "product_supplier_relationships"):
            for index, row in self.states[dataset].values.iterrows():
                position = int(index)
                if (
                    self._candidate(dataset, position)
                    and row["valid_to"] is not None
                    and row["valid_from"] >= row["valid_to"]
                ):
                    self._quarantine(
                        dataset, position, "DQ13", "effective interval is empty or reversed"
                    )
        for index, row in self.states["salespeople"].values.iterrows():
            position = int(index)
            if not self._candidate("salespeople", position):
                continue
            if row["termination_date"] is not None and row["hire_date"] >= row["termination_date"]:
                self._quarantine("salespeople", position, "DQ06", "invalid employment dates")
            if row["manager_salesperson_id"] == row["salesperson_id"]:
                self._quarantine("salespeople", position, "DQ10", "salesperson cannot manage self")
        for index, row in self.states["products"].values.iterrows():
            position = int(index)
            if not self._candidate("products", position):
                continue
            if (
                row["reference_list_unit_price_eur"] <= ZERO
                or row["reference_standard_unit_cost_eur"] <= ZERO
            ):
                self._quarantine(
                    "products", position, "DQ11", "catalogue price and cost must be positive"
                )
            expected_discontinued = row["lifecycle_status"] == "discontinued"
            if expected_discontinued != (row["discontinued_date"] is not None):
                self._quarantine("products", position, "DQ06", "product lifecycle date mismatch")
        for index, row in self.states["orders"].values.iterrows():
            position = int(index)
            if not self._candidate("orders", position):
                continue
            status = row["order_status"]
            dates_valid = (
                (
                    status == "placed"
                    and row["fulfilled_date"] is None
                    and row["cancelled_date"] is None
                )
                or (
                    status == "fulfilled"
                    and row["fulfilled_date"] is not None
                    and row["cancelled_date"] is None
                )
                or (
                    status == "cancelled"
                    and row["cancelled_date"] is not None
                    and row["fulfilled_date"] is None
                )
            )
            event_date = row["fulfilled_date"] if status == "fulfilled" else row["cancelled_date"]
            if not dates_valid or (event_date is not None and event_date < row["order_date"]):
                self._quarantine(
                    "orders", position, "DQ06", "order status/date contract is invalid"
                )
            if row["outbound_freight_eur"] < ZERO or (
                status != "fulfilled" and row["outbound_freight_eur"] != ZERO
            ):
                self._quarantine("orders", position, "DQ11", "invalid outbound freight posting")
        state = self.states["order_lines"]
        for index, row in state.values.iterrows():
            position = int(index)
            if (
                not self._candidate("order_lines", position)
                and state.status[position] != "quarantined"
            ):
                continue

            line_number = row["line_number"]
            quantity = row["quantity_ordered"]
            if (
                isinstance(line_number, int)
                and isinstance(quantity, int)
                and (line_number <= 0 or quantity <= 0)
            ):
                self._quarantine_once(
                    "order_lines",
                    position,
                    "DQ07",
                    "sold quantity and line number must be positive",
                )
            discount = row["discount_rate"]
            if isinstance(discount, Decimal) and not (
                Decimal("0") <= discount <= Decimal("1")
            ):
                self._quarantine_once(
                    "order_lines", position, "DQ08", "discount is outside [0,1]"
                )
            if row["unit_cost_eur"] is None and state.raw.at[position, "unit_cost_eur"] == "":
                self._quarantine_once(
                    "order_lines", position, "DQ09", "historical unit cost is missing"
                )
            monetary = [
                column
                for column in UNIT_DECIMAL_COLUMNS | MONEY_DECIMAL_COLUMNS
                if column in row.index and isinstance(row[column], Decimal)
            ]
            if any(row[column] < ZERO for column in monetary):
                self._quarantine_once(
                    "order_lines", position, "DQ11", "sale monetary values must be nonnegative"
                )
            expected_rate = (
                Decimal("0.200000")
                if row["vat_treatment_code"] == "sim_standard"
                else Decimal("0.000000")
            )
            vat_rate = row["vat_rate_fraction"]
            jurisdiction = row["tax_jurisdiction_code"]
            if (
                (isinstance(vat_rate, Decimal) and vat_rate != expected_rate)
                or (
                    isinstance(jurisdiction, str)
                    and jurisdiction not in {"LT", "LV", "EE"}
                )
            ):
                self._quarantine_once(
                    "order_lines", position, "DQ11", "invalid explicit VAT contract"
                )
        self._validate_return_states()
        for index, row in self.states["sales_targets"].values.iterrows():
            position = int(index)
            if not self._candidate("sales_targets", position):
                continue
            salesperson_required = row["allocation_type"] == "salesperson"
            if salesperson_required != (row["salesperson_id"] is not None):
                self._quarantine("sales_targets", position, "DQ14", "invalid target allocation key")
            if row["target_month"].day != 1 or row["target_value"] < ZERO:
                self._quarantine("sales_targets", position, "DQ14", "invalid target month or value")

    def _validate_return_states(self) -> None:
        zero_fields = (
            "posted_refund_ex_vat_eur",
            "posted_recovered_cogs_eur",
            "posted_vat_reversal_eur",
        )
        for index, row in self.states["returns"].values.iterrows():
            position = int(index)
            if not self._candidate("returns", position):
                continue
            status = row["return_status"]
            valid = row["requested_quantity"] > 0
            if status == "requested":
                valid &= row["accepted_quantity"] is None and row["return_decision_date"] is None
                valid &= (
                    row["restocking_outcome_code"] is None and row["refund_unit_ex_vat_eur"] is None
                )
            elif status == "rejected":
                valid &= (
                    row["accepted_quantity"] is None and row["return_decision_date"] is not None
                )
                valid &= (
                    row["restocking_outcome_code"] is None and row["refund_unit_ex_vat_eur"] is None
                )
            elif status == "accepted":
                valid &= (
                    row["accepted_quantity"] is not None
                    and 0 < row["accepted_quantity"] <= row["requested_quantity"]
                    and row["return_decision_date"] is not None
                    and row["restocking_outcome_code"] in {"restockable", "written_off"}
                    and row["refund_unit_ex_vat_eur"] is not None
                )
            if status != "accepted" and any(row[column] != ZERO for column in zero_fields):
                valid = False
            if row["returns_freight_eur"] < ZERO or (
                status != "accepted" and row["returns_freight_eur"] != ZERO
            ):
                valid = False
            if (
                row["return_decision_date"] is not None
                and row["return_decision_date"] < row["return_requested_date"]
            ):
                valid = False
            if not valid:
                self._quarantine("returns", position, "DQ12", "return lifecycle state is invalid")

    @staticmethod
    def _overlap(first: pd.Series, second: pd.Series) -> bool:
        first_end = first["valid_to"]
        second_end = second["valid_to"]
        return (first_end is None or second["valid_from"] < first_end) and (
            second_end is None or first["valid_from"] < second_end
        )

    def validate_intervals(self) -> None:
        checks = (
            ("customer_attribute_history", ("customer_id",), None),
            ("customer_account_assignments", ("customer_id",), None),
            ("product_supplier_relationships", ("product_id", "supplier_id"), None),
            ("product_supplier_relationships", ("product_id",), "primary"),
        )
        for dataset, keys, relationship_filter in checks:
            state = self.states[dataset]
            groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
            for index, row in state.values.iterrows():
                position = int(index)
                if not self._candidate(dataset, position):
                    continue
                if relationship_filter and row["relationship_type"] != relationship_filter:
                    continue
                groups[tuple(row[key] for key in keys)].append(position)
            for group, indexes in groups.items():
                ordered = sorted(
                    indexes,
                    key=lambda item: (
                        state.values.at[item, "valid_from"],
                        str(state.values.iloc[item, 0]),
                    ),
                )
                conflicts: set[int] = set()
                for offset, left in enumerate(ordered):
                    for right in ordered[offset + 1 :]:
                        if self._overlap(state.values.iloc[left], state.values.iloc[right]):
                            conflicts.update((left, right))
                        elif state.values.at[right, "valid_from"] >= (
                            state.values.at[left, "valid_to"] or date.max
                        ):
                            break
                for index in sorted(conflicts):
                    self._quarantine(
                        dataset, index, "DQ13", f"overlapping effective intervals for {group!r}"
                    )

        self._validate_required_history_coverage()

    def _validate_required_history_coverage(self) -> None:
        """Fail closed when conflict removal breaks mandatory temporal coverage."""

        customer_state = self.states["customers"]
        history_state = self.states["customer_attribute_history"]
        history_groups: dict[str, list[int]] = defaultdict(list)
        for index, row in history_state.values.iterrows():
            if self._candidate("customer_attribute_history", int(index)):
                history_groups[row["customer_id"]].append(int(index))
        for index, customer in customer_state.values.iterrows():
            position = int(index)
            if not self._candidate("customers", position):
                continue
            indexes = sorted(
                history_groups.get(customer["customer_id"], []),
                key=lambda item: history_state.values.at[item, "valid_from"],
            )
            rows = [history_state.values.iloc[item] for item in indexes]
            complete = bool(rows) and rows[0]["valid_from"] <= customer["acquisition_date"]
            complete &= sum(row["valid_to"] is None for row in rows) == 1
            complete &= all(
                left["valid_to"] == right["valid_from"] for left, right in pairwise(rows)
            )
            complete &= bool(rows) and rows[-1]["valid_to"] is None
            if complete:
                continue
            for history_index in indexes:
                self._quarantine(
                    "customer_attribute_history",
                    history_index,
                    "DQ13",
                    "customer history no longer has continuous mandatory coverage",
                )
            self._quarantine(
                "customers",
                position,
                "DQ10",
                "customer has no valid continuous SCD history",
                dependency=f"customer_attribute_history:{customer['customer_id']}",
            )

        product_state = self.states["products"]
        supplier_state = self.states["product_supplier_relationships"]
        primary_groups: dict[str, list[int]] = defaultdict(list)
        for index, row in supplier_state.values.iterrows():
            if (
                self._candidate("product_supplier_relationships", int(index))
                and row["relationship_type"] == "primary"
            ):
                primary_groups[row["product_id"]].append(int(index))
        for index, product in product_state.values.iterrows():
            position = int(index)
            if not self._candidate("products", position):
                continue
            indexes = sorted(
                primary_groups.get(product["product_id"], []),
                key=lambda item: supplier_state.values.at[item, "valid_from"],
            )
            rows = [supplier_state.values.iloc[item] for item in indexes]
            complete = bool(rows) and rows[0]["valid_from"] <= product["launch_date"]
            complete &= all(
                left["valid_to"] == right["valid_from"] for left, right in pairwise(rows)
            )
            required_end = product["discontinued_date"]
            if rows:
                final_end = rows[-1]["valid_to"]
                complete &= final_end is None or (
                    required_end is not None and final_end >= required_end
                )
            if complete:
                continue
            for relationship_index in indexes:
                self._quarantine(
                    "product_supplier_relationships",
                    relationship_index,
                    "DQ13",
                    "product no longer has continuous primary supplier coverage",
                )
            self._quarantine(
                "products",
                position,
                "DQ10",
                "product has no valid continuous primary supplier history",
                dependency=f"product_supplier_relationships:{product['product_id']}",
            )

    def propagate_foreign_keys(self) -> None:
        changed = True
        while changed:
            changed = False
            valid_keys = {
                name: {
                    state.values.at[index, PRIMARY_KEYS[name]]
                    for index in range(len(state.values))
                    if self._candidate(name, index)
                }
                for name, state in self.states.items()
            }
            for child, relationships in FOREIGN_KEYS.items():
                state = self.states[child]
                for index, row in state.values.iterrows():
                    position = int(index)
                    if not self._candidate(child, position):
                        continue
                    for column, parent, _parent_key in relationships:
                        value = row[column]
                        if value is None:
                            continue
                        if value not in valid_keys[parent]:
                            self._quarantine(
                                child,
                                position,
                                "DQ10",
                                f"orphan {column}={value!r}; parent unavailable in valid {parent}",
                                dependency=f"{parent}:{value}",
                            )
                            changed = True
                            break

    def _audit_quarantined_direct_foreign_keys(self) -> None:
        """Record direct orphans on rows already quarantined by an independent rule."""

        all_keys = {
            name: set(state.values[PRIMARY_KEYS[name]]) for name, state in self.states.items()
        }
        for child, relationships in FOREIGN_KEYS.items():
            state = self.states[child]
            for index, row in state.values.iterrows():
                position = int(index)
                if state.status[position] != "quarantined" or "DQ10" in state.violations[position]:
                    continue
                for column, parent, _parent_key in relationships:
                    value = row[column]
                    if value is None or value in all_keys[parent]:
                        continue
                    self._quarantine(
                        child,
                        position,
                        "DQ10",
                        f"orphan {column}={value!r}; parent unavailable in valid {parent}",
                        dependency=f"{parent}:{value}",
                    )
                    break

    def _active_rows(self, dataset: str) -> pd.DataFrame:
        state = self.states[dataset]
        indexes = [index for index in range(len(state.values)) if self._candidate(dataset, index)]
        return state.values.iloc[indexes]

    def validate_temporal_relationships(self) -> None:
        customers = self._active_rows("customers").set_index("customer_id")
        people = self._active_rows("salespeople").set_index("salesperson_id")
        products = self._active_rows("products").set_index("product_id")
        history = self._active_rows("customer_attribute_history")
        histories: dict[str, list[pd.Series]] = defaultdict(list)
        for _, row in history.iterrows():
            histories[row["customer_id"]].append(row)

        for index, row in self.states["customer_account_assignments"].values.iterrows():
            position = int(index)
            if not self._candidate("customer_account_assignments", position):
                continue
            customer = customers.loc[row["customer_id"]]
            person = people.loc[row["salesperson_id"]]
            invalid = (
                row["valid_from"] < customer["acquisition_date"]
                or row["valid_from"] < person["hire_date"]
            )
            if person["termination_date"] is not None:
                invalid |= row["valid_to"] is None or row["valid_to"] > person["termination_date"]
            if invalid:
                self._quarantine(
                    "customer_account_assignments",
                    position,
                    "DQ13",
                    "ownership interval exceeds customer/employment validity",
                )

        orders = self.states["orders"]
        for index, row in orders.values.iterrows():
            position = int(index)
            if not self._candidate("orders", position):
                continue
            customer = customers.loc[row["customer_id"]]
            event_date = (
                row["fulfilled_date"] if row["order_status"] == "fulfilled" else row["order_date"]
            )
            matching_history = [
                version
                for version in histories.get(row["customer_id"], [])
                if version["valid_from"] <= event_date
                and (version["valid_to"] is None or event_date < version["valid_to"])
            ]
            invalid_reason = None
            if row["order_date"] < customer["acquisition_date"]:
                invalid_reason = "order predates customer acquisition"
            elif len(matching_history) != 1:
                invalid_reason = "order has no unique time-valid customer history parent"
            elif row["credited_salesperson_id"] is not None:
                person = people.loc[row["credited_salesperson_id"]]
                if event_date < person["hire_date"] or (
                    person["termination_date"] is not None
                    and event_date >= person["termination_date"]
                ):
                    invalid_reason = "credited salesperson is not eligible on event date"
            if event_date > self.configuration.application.reporting.extension_end:
                invalid_reason = "order event is beyond configured operational extension"
            if invalid_reason:
                self._quarantine(
                    "orders", position, "DQ10", invalid_reason, dependency="temporal parent"
                )

        self.propagate_foreign_keys()
        orders_by_id = self._active_rows("orders").set_index("order_id")
        for index, row in self.states["order_lines"].values.iterrows():
            position = int(index)
            if not self._candidate("order_lines", position):
                continue
            order = orders_by_id.loc[row["order_id"]]
            product = products.loc[row["product_id"]]
            dates = [order["order_date"]]
            if order["fulfilled_date"] is not None:
                dates.append(order["fulfilled_date"])
            if any(
                event_date < product["launch_date"]
                or (
                    product["discontinued_date"] is not None
                    and event_date >= product["discontinued_date"]
                )
                for event_date in dates
            ):
                self._quarantine(
                    "order_lines", position, "DQ10", "product is not eligible on order event date"
                )

        self.propagate_foreign_keys()
        lines = self._active_rows("order_lines").set_index("order_line_id")
        orders_by_id = self._active_rows("orders").set_index("order_id")
        for index, row in self.states["returns"].values.iterrows():
            position = int(index)
            if not self._candidate("returns", position):
                continue
            line = lines.loc[row["order_line_id"]]
            order = orders_by_id.loc[line["order_id"]]
            decision = row["return_decision_date"]
            invalid = (
                order["order_status"] != "fulfilled"
                or row["return_requested_date"] < order["fulfilled_date"]
                or (decision is not None and decision < order["fulfilled_date"])
                or row["return_requested_date"]
                > self.configuration.application.reporting.extension_end
                or (
                    decision is not None
                    and decision > self.configuration.application.reporting.extension_end
                )
            )
            if invalid:
                self._quarantine(
                    "returns", position, "DQ12", "return is not time-valid against fulfilled line"
                )

        for index, row in self.states["sales_targets"].values.iterrows():
            position = int(index)
            if not self._candidate("sales_targets", position) or row["salesperson_id"] is None:
                continue
            person = people.loc[row["salesperson_id"]]
            month_end = (row["target_month"].replace(day=28) + timedelta(days=4)).replace(day=1)
            eligible = person["hire_date"] < month_end and (
                person["termination_date"] is None
                or person["termination_date"] > row["target_month"]
            )
            if not eligible or person["primary_country_code"] != row["country_code"]:
                self._quarantine(
                    "sales_targets",
                    position,
                    "DQ14",
                    "target allocation is not employment/country eligible",
                )

    def validate_financials(self) -> None:
        orders = self._active_rows("orders").set_index("order_id")
        for index, row in self.states["order_lines"].values.iterrows():
            position = int(index)
            if not self._candidate("order_lines", position):
                continue
            order = orders.loc[row["order_id"]]
            expected_realised = quantize_unit(
                row["list_unit_price_eur"] * (Decimal("1") - row["discount_rate"])
            )
            expected = sale_postings(
                row["quantity_ordered"],
                row["list_unit_price_eur"],
                row["discount_rate"],
                row["unit_cost_eur"],
                row["vat_rate_fraction"],
                fulfilled=order["order_status"] == "fulfilled",
            )
            actual = tuple(
                row[column]
                for column in (
                    "posted_list_value_eur",
                    "posted_discount_eur",
                    "posted_net_sales_eur",
                    "posted_cogs_eur",
                    "posted_taxable_base_eur",
                    "posted_vat_amount_eur",
                    "posted_invoice_total_eur",
                )
            )
            if row["realised_unit_price_eur"] != expected_realised or actual != expected:
                self._quarantine(
                    "order_lines",
                    position,
                    "DQ11",
                    "sale financial postings do not reconcile exactly",
                )

        self.propagate_foreign_keys()
        orders = self._active_rows("orders")
        children_by_order = _financial_children_by_order(self._active_rows("order_lines"))
        for source_index, order in orders.iterrows():
            freight_inputs = children_by_order.get(order["order_id"])
            if freight_inputs is None:
                continue
            try:
                allocations = allocate_freight(
                    order["outbound_freight_eur"],
                    freight_inputs[0],
                    freight_inputs[1],
                )
            except ValueError:
                allocations = []
            if not allocations or sum(allocations, ZERO) != order["outbound_freight_eur"]:
                self._quarantine(
                    "orders",
                    int(source_index),
                    "DQ11",
                    "outbound freight cannot be consistently allocated",
                )

        self.propagate_foreign_keys()
        lines = self._active_rows("order_lines").set_index("order_line_id")
        for index, row in self.states["returns"].values.iterrows():
            position = int(index)
            if not self._candidate("returns", position):
                continue
            line = lines.loc[row["order_line_id"]]
            try:
                expected = return_postings(
                    row["accepted_quantity"],
                    row["refund_unit_ex_vat_eur"],
                    line["realised_unit_price_eur"],
                    line["unit_cost_eur"],
                    line["vat_rate_fraction"],
                    row["restocking_outcome_code"],
                    accepted=row["return_status"] == "accepted",
                )
            except ValueError:
                self._quarantine(
                    "returns", position, "DQ12", "return refund exceeds original-price contract"
                )
                continue
            actual = (
                row["posted_refund_ex_vat_eur"],
                row["posted_recovered_cogs_eur"],
                row["posted_vat_reversal_eur"],
            )
            if actual != expected:
                self._quarantine(
                    "returns",
                    position,
                    "DQ12",
                    "return financial postings do not reconcile exactly",
                )

    def propagate_required_children(self) -> None:
        """Remove fulfilled headers whose valid line set was eliminated by failures."""

        valid_line_orders = set(self._active_rows("order_lines")["order_id"])
        state = self.states["orders"]
        changed = False
        for index, row in state.values.iterrows():
            position = int(index)
            if (
                self._candidate("orders", position)
                and row["order_status"] == "fulfilled"
                and row["order_id"] not in valid_line_orders
            ):
                self._quarantine(
                    "orders",
                    position,
                    "DQ10",
                    "fulfilled order has no valid dependent order line",
                    dependency=f"order_lines:{row['order_id']}",
                )
                changed = True
        if changed:
            self.propagate_foreign_keys()

    def validate_return_caps(self) -> None:
        lines = self._active_rows("order_lines").set_index("order_line_id")
        state = self.states["returns"]
        active_indexes = [
            index for index in range(len(state.values)) if self._candidate("returns", index)
        ]
        active = state.values.iloc[active_indexes]
        sold_quantities = {
            line_id: int(row["quantity_ordered"]) for line_id, row in lines.iterrows()
        }
        for index in accepted_return_cap_violations(active, sold_quantities):
            line_id = state.values.at[index, "order_line_id"]
            sold = sold_quantities[line_id]
            self._quarantine(
                "returns",
                int(index),
                "DQ12",
                f"ordered cumulative accepted quantity would exceed sold quantity {sold}",
            )

    def enforce_secondary_uniqueness(self) -> None:
        checks = (
            ("cities", ("region_id", "city_name")),
            ("products", ("sku",)),
            ("order_lines", ("order_id", "line_number")),
        )
        for dataset, columns in checks:
            state = self.states[dataset]
            groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
            for index, row in state.values.iterrows():
                position = int(index)
                if self._candidate(dataset, position):
                    groups[tuple(row[column] for column in columns)].append(position)
            for key, indexes in groups.items():
                if len(indexes) > 1:
                    for index in indexes:
                        self._quarantine(
                            dataset, index, "DQ02", f"conflicting unique business grain {key!r}"
                        )

    def run(self) -> QualityResult:
        self.detect_duplicates()
        self.apply_repairs()
        self.parse_and_validate_rows()
        self._audit_quarantined_direct_foreign_keys()
        self.validate_intervals()
        self.propagate_foreign_keys()
        self.validate_temporal_relationships()
        self.propagate_foreign_keys()
        self.validate_financials()
        self.propagate_foreign_keys()
        self.propagate_required_children()
        self.validate_return_caps()
        self.enforce_secondary_uniqueness()
        self.propagate_foreign_keys()
        return self._build_result()

    def _build_result(self) -> QualityResult:
        silver: dict[str, pd.DataFrame] = {}
        quarantine: dict[str, pd.DataFrame] = {}
        disposition_rows: list[dict[str, Any]] = []
        for name, state in self.states.items():
            accepted = [
                index
                for index, status in enumerate(state.status)
                if status in {"accepted_unchanged", "repaired"}
            ]
            silver[name] = state.values.iloc[accepted].reset_index(drop=True)
            try:
                silver_pandera_schema(name).validate(silver[name], lazy=True)
            except pandera.errors.SchemaErrors as exc:
                raise DataQualityError(
                    f"critical Pandera constraint remained in prospective Silver: {name}"
                ) from exc
            quarantine_rows = []
            for index, status in enumerate(state.status):
                if status != "quarantined":
                    continue
                provenance = state.provenance.iloc[index]
                quarantine_rows.append(
                    {
                        "batch_id": self.batch_id,
                        "dataset": name,
                        "source_file": provenance["source_file"],
                        "source_row": int(provenance["raw_row_number_int"]),
                        "origin_source_row": provenance["source_row"],
                        "business_key": state.raw.at[index, PRIMARY_KEYS[name]],
                        "violation_ids": state.violations[index],
                        "reason": " | ".join(state.reasons[index]),
                        "severity": "high",
                        "dependency_context": " | ".join(state.dependency_context[index]),
                        "original_payload": json.dumps(
                            state.raw.iloc[index].to_dict(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                )
            quarantine[name] = pd.DataFrame(
                quarantine_rows,
                columns=(
                    "batch_id",
                    "dataset",
                    "source_file",
                    "source_row",
                    "origin_source_row",
                    "business_key",
                    "violation_ids",
                    "reason",
                    "severity",
                    "dependency_context",
                    "original_payload",
                ),
            )
            counts = {status: state.status.count(status) for status in _STATUS_RANK}
            received = len(state.status)
            accounted = sum(counts.values())
            if received != accounted:
                raise DataQualityError(f"source-row conservation failed for {name}")
            disposition_rows.append(
                {
                    "scope": "dataset",
                    "scope_id": name,
                    "received": received,
                    **counts,
                    "conservation_passed": True,
                }
            )

        totals = {
            column: sum(int(row[column]) for row in disposition_rows)
            for column in ("received", *_STATUS_RANK)
        }
        if totals["received"] != sum(totals[status] for status in _STATUS_RANK):
            raise DataQualityError("source-row conservation failed for raw batch")
        for scope, scope_id in (("batch", self.batch_id), ("run", self.raw_manifest_hash[:16])):
            disposition_rows.append(
                {"scope": scope, "scope_id": scope_id, **totals, "conservation_passed": True}
            )
        dispositions = pd.DataFrame(disposition_rows)

        output_hashes: dict[tuple[str, int], str] = {}
        for name, state in self.states.items():
            for index in range(len(state.values)):
                if self._candidate(name, index):
                    output_hashes[
                        (name, int(state.provenance.iloc[index]["raw_row_number_int"]))
                    ] = _payload_hash(state.values.iloc[index].to_dict())
        for row in self.decision_rows:
            row["output_hash"] = output_hashes.get((row["dataset"], row["source_row"]), "")
        decisions = (
            pd.DataFrame(
                self.decision_rows,
                columns=(
                    "batch_id",
                    "dataset",
                    "source_row",
                    "rule_id",
                    "severity",
                    "action",
                    "original_hash",
                    "output_hash",
                    "reason",
                    "repair_details",
                ),
            )
            .sort_values(["dataset", "source_row", "rule_id", "action", "reason"])
            .reset_index(drop=True)
        )

        manifest = self._base_manifest(silver, dispositions, decisions)
        return QualityResult(
            silver=silver,
            quarantine=quarantine,
            decisions=decisions,
            dispositions=dispositions,
            manifest=manifest,
            raw_manifest_sha256=self.raw_manifest_hash,
        )

    def _base_manifest(
        self,
        silver: dict[str, pd.DataFrame],
        dispositions: pd.DataFrame,
        decisions: pd.DataFrame,
    ) -> dict[str, Any]:
        dataset_counts = dispositions.loc[dispositions["scope"] == "dataset"].set_index("scope_id")
        rule_summary = (
            decisions.groupby(["rule_id", "action"], sort=True).size().rename("count").reset_index()
            if not decisions.empty
            else pd.DataFrame(columns=("rule_id", "action", "count"))
        )
        return {
            "schema_id": "bsi.silver_publication",
            "schema_version": 1,
            "stage": "validate",
            "pipeline_version": __version__,
            "quality_status": "passed",
            "source_batch_id": self.batch_id,
            "raw_input_manifest_sha256": self.raw_manifest_hash,
            "config_hash": self.configuration.deterministic_hash(),
            "stage_duration_seconds": 0.0,
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    package: importlib.metadata.version(package)
                    for package in ("pandas", "pandera", "pyarrow")
                },
            },
            "datasets": {
                name: {
                    "schema_id": f"bsi.silver.{name}",
                    "schema_version": 1,
                    "path": f"{name}.parquet",
                    "row_count": len(frame),
                    "columns": list(frame.columns),
                    "quarantine_count": int(dataset_counts.loc[name, "quarantined"]),
                    "repair_count": int(dataset_counts.loc[name, "repaired"]),
                    "duplicate_discard_count": int(
                        dataset_counts.loc[name, "discarded_exact_duplicate"]
                    ),
                }
                for name, frame in silver.items()
            },
            "validation_summary": {
                "source_row_conservation": "passed",
                "critical_violations_in_silver": 0,
                "rule_decisions": rule_summary.to_dict(orient="records"),
                "cascade_quarantine_count": sum(
                    bool(context)
                    for state in self.states.values()
                    for context in state.dependency_context
                ),
            },
            "artifacts": {
                "decision_log_csv": "dq_decision_log.csv",
                "decision_log_parquet": "dq_decision_log.parquet",
                "disposition_counts_csv": "source_row_dispositions.csv",
                "disposition_counts_parquet": "source_row_dispositions.parquet",
            },
        }


def process_raw_batch(
    configuration: ProjectConfiguration, raw_directory: str | Path
) -> QualityResult:
    """Validate a raw batch without reading any private injection registry."""

    source = Path(raw_directory)
    states, manifest, manifest_hash = _read_raw_batch(configuration, source)
    return _QualityEngine(configuration, states, manifest, manifest_hash).run()


def _write_parquet(frame: pd.DataFrame, path: Path, schema: pa.Schema | None = None) -> None:
    if schema is None:
        table = pa.Table.from_pandas(frame, preserve_index=False)
    else:
        arrays = []
        for field in schema:
            values = [
                None if value is None or pd.isna(value) else value for value in frame[field.name]
            ]
            arrays.append(pa.array(values, type=field.type))
        table = pa.Table.from_arrays(arrays, schema=schema)
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    )


def _prepare_publication(result: QualityResult, root: Path) -> tuple[Path, Path, dict[str, Any]]:
    silver = root / "silver"
    quarantine = root / "quarantine"
    silver.mkdir()
    quarantine.mkdir()
    (silver / ".gitkeep").touch()
    (quarantine / ".gitkeep").touch()
    manifest = json.loads(json.dumps(result.manifest))
    for name, frame in result.silver.items():
        path = silver / f"{name}.parquet"
        _write_parquet(frame, path, arrow_schema(name))
        manifest["datasets"][name]["sha256"] = _file_hash(path)
        table = pq.read_table(path)
        if table.schema != arrow_schema(name):
            raise DataQualityError(f"published Silver schema verification failed: {name}")
    result.decisions.to_csv(silver / "dq_decision_log.csv", index=False, lineterminator="\n")
    _write_parquet(result.decisions, silver / "dq_decision_log.parquet")
    result.dispositions.to_csv(
        silver / "source_row_dispositions.csv", index=False, lineterminator="\n"
    )
    _write_parquet(result.dispositions, silver / "source_row_dispositions.parquet")
    for key, relative in list(manifest["artifacts"].items()):
        manifest["artifacts"][f"{key}_sha256"] = _file_hash(silver / relative)

    quarantine_files = {}
    for name, frame in result.quarantine.items():
        if frame.empty:
            continue
        csv_path = quarantine / f"{name}.csv"
        parquet_path = quarantine / f"{name}.parquet"
        serialised = frame.copy()
        serialised["violation_ids"] = serialised["violation_ids"].map(
            lambda values: json.dumps(values, separators=(",", ":"))
        )
        serialised.to_csv(csv_path, index=False, lineterminator="\n")
        _write_parquet(frame, parquet_path)
        quarantine_files[name] = {
            "row_count": len(frame),
            "csv_path": csv_path.name,
            "csv_sha256": _file_hash(csv_path),
            "parquet_path": parquet_path.name,
            "parquet_sha256": _file_hash(parquet_path),
        }
    quarantine_manifest = {
        "schema_id": "bsi.quarantine_publication",
        "schema_version": 1,
        "stage": "validate",
        "source_batch_id": result.manifest["source_batch_id"],
        "raw_input_manifest_sha256": result.raw_manifest_sha256,
        "quality_status": "passed_with_quarantine",
        "datasets": quarantine_files,
    }
    (quarantine / "quarantine_manifest.json").write_text(
        json.dumps(quarantine_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest["quarantine_manifest_sha256"] = _file_hash(quarantine / "quarantine_manifest.json")
    (silver / "silver_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return silver, quarantine, manifest


def _has_material_output(path: Path) -> bool:
    return path.exists() and (
        not path.is_dir() or any(item.name != ".gitkeep" for item in path.iterdir())
    )


def _atomic_replace(staged: tuple[Path, Path], targets: tuple[Path, Path], force: bool) -> None:
    for target in targets:
        if _has_material_output(target) and not force:
            raise DataQualityError(f"output already exists; use --force: {target}")
    backups: list[tuple[Path, Path]] = []
    published: list[Path] = []
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = target.with_name(f".{target.name}.previous-{os.getpid()}")
                if backup.exists():
                    raise DataQualityError(f"publication backup path already exists: {backup}")
                os.replace(target, backup)
                backups.append((backup, target))
        for source, target in zip(staged, targets, strict=True):
            os.replace(source, target)
            published.append(target)
    except Exception:
        for target in published:
            if target.exists():
                shutil.rmtree(target)
        for backup, target in reversed(backups):
            if backup.exists():
                os.replace(backup, target)
        raise
    for backup, _target in backups:
        if backup.exists():
            shutil.rmtree(backup)


def publish_silver(
    configuration: ProjectConfiguration,
    raw_directory: str | Path,
    output_directory: str | Path,
    quarantine_directory: str | Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> QualityResult:
    """Run the quality engine and atomically publish Silver and quarantine outputs."""

    output = Path(output_directory)
    quarantine = Path(quarantine_directory)
    if publish and not force:
        for target in (output, quarantine):
            if _has_material_output(target):
                raise DataQualityError(f"output already exists; use --force: {target}")
    result = process_raw_batch(configuration, raw_directory)
    if not publish:
        return result
    common_parent = Path(os.path.commonpath([output.parent.resolve(), quarantine.parent.resolve()]))
    common_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".quality-", dir=common_parent) as temporary:
        staged_silver, staged_quarantine, published_manifest = _prepare_publication(
            result, Path(temporary)
        )
        _atomic_replace((staged_silver, staged_quarantine), (output, quarantine), force)
    return QualityResult(
        silver=result.silver,
        quarantine=result.quarantine,
        decisions=result.decisions,
        dispositions=result.dispositions,
        manifest=published_manifest,
        raw_manifest_sha256=result.raw_manifest_sha256,
        output_directory=output,
        quarantine_directory=quarantine,
    )
