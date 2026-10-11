"""Restricted SPEC-06 controls used only by generation and scenario QA.

The separation in this demonstrative local repository prevents accidental
lineage into public datasets; it is not a formal security boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PRIVATE_CONFIG_PATH = Path(__file__).parents[3] / "config" / "private_scenarios.yaml"
REGISTRY_COLUMNS = (
    "story_id",
    "mechanism",
    "target_entity_private_id",
    "start_date",
    "end_date",
    "target_effect",
    "baseline_comparator",
    "generation_seed_component",
    "validation_tolerance",
    "observed_effect",
)


@dataclass(frozen=True)
class ScenarioControl:
    story_id: str
    mechanism: str
    target: dict[str, Any]
    start_date: date
    end_date: date
    strength: dict[str, Any]
    baseline_comparator: str
    exposure_requirements: dict[str, Any]
    validation_thresholds: dict[str, float]
    seed_component: int


def load_scenario_controls(path: str | Path = PRIVATE_CONFIG_PATH) -> tuple[ScenarioControl, ...]:
    """Load and fail closed on the tracked private scenario configuration."""

    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("private scenario config must use schema_version 1")
    rows = payload.get("scenarios")
    if not isinstance(rows, list) or len(rows) != 15:
        raise ValueError("private scenario config must define exactly 15 scenarios")
    controls = []
    required = {
        "story_id",
        "mechanism",
        "target",
        "start_date",
        "end_date",
        "strength",
        "baseline_comparator",
        "exposure_requirements",
        "validation_thresholds",
        "seed_component",
    }
    for row in rows:
        if set(row) != required:
            raise ValueError(f"{row.get('story_id', '?')}: invalid private control fields")
        controls.append(ScenarioControl(**row))
    if tuple(control.story_id for control in controls) != tuple(
        f"S{number:02d}" for number in range(1, 16)
    ):
        raise ValueError("private scenarios must be ordered S01 through S15")
    if len({control.seed_component for control in controls}) != 15:
        raise ValueError("scenario seed components must be unique")
    return tuple(controls)


def controls_hash(path: str | Path = PRIVATE_CONFIG_PATH) -> str:
    """Return a content hash for private reproducibility records."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scenario_rng(master_seed: int, control: ScenarioControl) -> np.random.Generator:
    sequence = np.random.SeedSequence(master_seed, spawn_key=(11, control.seed_component))
    return np.random.Generator(np.random.PCG64(sequence))


def resolve_targets(
    controls: tuple[ScenarioControl, ...], tables: dict[str, pd.DataFrame]
) -> dict[str, str]:
    """Resolve private selectors to stable synthetic entity/cohort identifiers."""

    history = (
        tables["customer_attribute_history"]
        .sort_values("valid_from")
        .groupby("customer_id", sort=False)
        .first()
    )
    customers = tables["customers"].set_index("customer_id")
    people = tables["salespeople"]
    resolved: dict[str, str] = {}
    for control in controls:
        target = control.target
        kind = target["kind"]
        if kind == "large_customer":
            eligible = (
                history[
                    (history["home_country_code"] == target["country"])
                    & (history["company_size_band"] == "large")
                ]
                .join(customers[["acquisition_date"]])
                .sort_values(["acquisition_date"], kind="stable")
            )
            if eligible.empty:
                eligible = history[history["home_country_code"] == target["country"]]
            resolved[control.story_id] = str(eligible.index[0])
        elif kind == "salesperson":
            eligible = people[people["primary_country_code"] == target["country"]].sort_values(
                ["hire_date", "salesperson_id"]
            )
            resolved[control.story_id] = str(
                eligible.iloc[int(target["ordinal"])]["salesperson_id"]
            )
        elif kind == "country_top_customers":
            market = history[history["home_country_code"] == target["country"]]
            preferred = market[
                (market["company_size_band"] == "large") & (market["segment_code"] == "ENTERPRISE")
            ]
            if len(preferred) < int(target["count"]):
                preferred = market[market["company_size_band"] == "large"]
            eligible = preferred.join(customers[["acquisition_date"]]).sort_values(
                ["acquisition_date"], kind="stable"
            )
            resolved[control.story_id] = "+".join(
                eligible.index[: int(target["count"])].astype(str)
            )
        elif kind == "category_pair":
            resolved[control.story_id] = "+".join(target["ids"])
        else:
            resolved[control.story_id] = str(target.get("id", target.get("country", kind)))
    return resolved


def build_truth_registry(
    controls: tuple[ScenarioControl, ...], tables: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Build deterministic restricted truth without observing generated outcomes."""

    targets = resolve_targets(controls, tables)
    rows = []
    for control in controls:
        rows.append(
            (
                control.story_id,
                control.mechanism,
                targets[control.story_id],
                control.start_date,
                control.end_date,
                json.dumps(control.strength, sort_keys=True, separators=(",", ":")),
                control.baseline_comparator,
                control.seed_component,
                json.dumps(
                    {
                        "exposure": control.exposure_requirements,
                        "thresholds": control.validation_thresholds,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                None,
            )
        )
    return pd.DataFrame(rows, columns=REGISTRY_COLUMNS)


def active(control: ScenarioControl, event_date: date) -> bool:
    return control.start_date <= event_date <= control.end_date


def progress(control: ScenarioControl, event_date: date) -> float:
    if event_date <= control.start_date:
        return 0.0
    duration = max(1, (control.end_date - control.start_date).days)
    return min(1.0, (event_date - control.start_date).days / duration)
