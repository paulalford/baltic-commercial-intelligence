"""Optional QA-oracle evaluation kept separate from production validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def evaluate_private_registry(
    private_registry: str | Path,
    decision_log: str | Path,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Compare planted defects with already-produced decisions.

    This function consumes only completed production artifacts. Its result cannot alter
    validation, disposition, quarantine, or Silver publication.
    """

    registry = pd.read_csv(private_registry, dtype=str, keep_default_na=False)
    decisions = pd.read_parquet(decision_log)
    required_registry = {
        "dataset",
        "defect_id",
        "raw_row_number",
        "expected_downstream_action",
    }
    required_decisions = {"dataset", "source_row", "rule_id", "action"}
    if not required_registry <= set(registry.columns):
        raise ValueError("private registry does not contain the required QA fields")
    if not required_decisions <= set(decisions.columns):
        raise ValueError("decision log does not contain the required audit fields")
    registry["raw_row_number"] = registry["raw_row_number"].astype(int)

    expected = {
        (row.dataset, int(row.raw_row_number), row.defect_id)
        for row in registry.itertuples(index=False)
    }
    predicted = {
        (row.dataset, int(row.source_row), row.rule_id) for row in decisions.itertuples(index=False)
    }
    true_positives = expected & predicted
    coverage = []
    for rule_id in sorted(registry["defect_id"].unique()):
        planted = {item for item in expected if item[2] == rule_id}
        detected = planted & predicted
        coverage.append(
            {
                "rule_id": rule_id,
                "planted": len(planted),
                "detected": len(detected),
                "recall": len(detected) / len(planted) if planted else None,
            }
        )

    expected_actions = registry.assign(
        expected_action=registry["expected_downstream_action"].map(_normalise_expected_action)
    )
    predicted_actions = decisions[["dataset", "source_row", "rule_id", "action"]].copy()
    predicted_actions["source_row"] = predicted_actions["source_row"].astype(int)
    joined = expected_actions.merge(
        predicted_actions,
        left_on=["dataset", "raw_row_number", "defect_id"],
        right_on=["dataset", "source_row", "rule_id"],
        how="left",
    )
    action_matches = int((joined["expected_action"] == joined["action"]).sum())
    report = {
        "schema_id": "bsi.private_dq_qa_evaluation",
        "schema_version": 1,
        "planted_instances": len(expected),
        "direct_detections": len(true_positives),
        "recall": len(true_positives) / len(expected) if expected else None,
        "direct_precision": len(true_positives) / len(predicted) if predicted else None,
        "action_accuracy": action_matches / len(joined) if len(joined) else None,
        "additional_rule_decisions_including_cascades": len(predicted - expected),
        "coverage": coverage,
        "production_influence": "none; post-publication measurement only",
    }
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return report


def _normalise_expected_action(value: str) -> str:
    lowered = value.casefold()
    if "discard" in lowered or "dedup" in lowered:
        return "discard_exact_duplicate"
    if "repair" in lowered or "normalise" in lowered:
        return "repair"
    return "quarantine"
