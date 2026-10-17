"""Versioned, stage-local implementation assumptions for SPEC-14."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyError(ValueError):
    """Opportunity policy is absent or invalid."""


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impact: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    actionability: float = Field(ge=0, le=1)
    urgency: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def approved_weights(self) -> Weights:
        if (self.impact, self.confidence, self.actionability, self.urgency) != (0.4, 0.3, 0.2, 0.1):
            raise ValueError("SPEC-14 scoring weights must remain 40/30/20/10")
        return self


class OpportunityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    method_version: str
    observation_months: int = Field(ge=1)
    minimum_customer_orders: int = Field(ge=1)
    lapse_cadence_multiplier: float = Field(ge=1)
    minimum_peer_accounts: int = Field(ge=2)
    minimum_pair_joint_baskets: int = Field(ge=30)
    minimum_pair_support: float = Field(ge=0.01, le=1)
    minimum_pair_lift: float = Field(ge=1)
    maximum_pair_q_value: float = Field(ge=0, le=1)
    minimum_context_baskets: int = Field(ge=1)
    minimum_product_units: int = Field(ge=1)
    minimum_defect_events: int = Field(ge=1)
    minimum_defect_quarter_events: int = Field(ge=1)
    candidate_limit_per_family: int = Field(ge=1)
    measurement_quality: dict[str, float]
    urgency_scores: dict[str, float]
    actionability_score_estimable: float = Field(ge=0, le=100)
    actionability_score_investigative: float = Field(ge=0, le=100)
    confidence_evidence_weight: float = Field(ge=0, le=1)
    confidence_history_weight: float = Field(ge=0, le=1)
    confidence_quality_weight: float = Field(ge=0, le=1)
    confidence_sensitivity_weight: float = Field(ge=0, le=1)
    annual_impact_cap_fraction: float = Field(gt=0, le=1)
    scenario_low: float = Field(ge=0, le=1)
    scenario_base: float = Field(ge=0, le=1)
    scenario_high: float = Field(ge=0, le=1)
    cross_sell_attach_low: float = Field(ge=0, le=1)
    cross_sell_attach_base: float = Field(ge=0, le=1)
    cross_sell_attach_high: float = Field(ge=0, le=1)
    margin_discount_gap: float = Field(ge=0, le=1)
    margin_unit_recovery_cap_fraction: float = Field(gt=0, le=1)
    growth_min_rate: float = Field(ge=0)
    defect_share_threshold: float = Field(ge=0, le=1)
    defect_peer_excess: float = Field(ge=0, le=1)
    concentration_top5_threshold: float = Field(ge=0, le=1)
    retention_decline_threshold: float = Field(ge=0, le=1)
    revenue_growth_threshold: float = Field(ge=0)
    margin_decline_points: float = Field(ge=0, le=1)
    return_quality_increase_points: float = Field(ge=0, le=1)
    adjusted_discount_increase_points: float = Field(ge=0, le=1)
    regional_revenue_decline: float = Field(ge=0, le=1)
    regional_channel_mix_shift: float = Field(ge=0, le=1)
    forecast_min_coverage95_for_high_severity: float = Field(ge=0, le=1)
    minimum_insight_postings: int = Field(ge=1)
    minimum_concentration_customers: int = Field(ge=2)
    minimum_cohort_size: int = Field(ge=1)
    minimum_cohort_groups: int = Field(ge=1)
    cohort_comparison_months: int = Field(ge=1)
    minimum_discount_products: int = Field(ge=1)
    minimum_forecast_evaluations: int = Field(ge=1)
    score_weights: Weights

    @model_validator(mode="after")
    def ordered_scenarios(self) -> OpportunityPolicy:
        if not (self.scenario_low <= self.scenario_base <= self.scenario_high):
            raise ValueError("impact scenarios must be ordered")
        if not (
            self.cross_sell_attach_low <= self.cross_sell_attach_base <= self.cross_sell_attach_high
        ):
            raise ValueError("attach scenarios must be ordered")
        families = {f"O{i:02d}" for i in range(1, 7)}
        if (
            set(self.measurement_quality) != families
            or set(self.urgency_scores) != families
            or any(not 0 <= x <= 1 for x in self.measurement_quality.values())
            or any(not 0 <= x <= 100 for x in self.urgency_scores.values())
        ):
            raise ValueError("quality and urgency must cover all six families")
        if (
            abs(
                sum(
                    (
                        self.confidence_evidence_weight,
                        self.confidence_history_weight,
                        self.confidence_quality_weight,
                        self.confidence_sensitivity_weight,
                    )
                )
                - 1
            )
            > 1e-9
        ):
            raise ValueError("confidence component weights must sum to one")
        if self.cohort_comparison_months < self.minimum_cohort_groups:
            raise ValueError("cohort comparison must contain the minimum cohort groups")
        return self

    def deterministic_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def load_policy(directory: Path) -> OpportunityPolicy:
    path = Path(directory) / "opportunities.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return OpportunityPolicy.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise PolicyError(f"invalid opportunity policy {path}: {exc}") from exc
