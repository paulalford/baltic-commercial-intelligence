"""Prequential, explainable seasonal commercial anomaly assessments."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import numpy as np

from baltic_sales_intelligence.forecasting.core import cents

METHOD_VERSION = "seasonal-median-mad-v1"
MIN_SEASONAL_HISTORY = 24
MIN_RESIDUAL_HISTORY = 12
ROBUST_SCALE = 1.4826
SCORE_THRESHOLD = 3.5


def _recommendation(metric: str) -> str:
    if metric == "net_revenue_eur":
        return "Review posting composition, customer mix, accepted returns and comparable months."
    return "Review revenue and gross-profit postings, product mix and comparable months."


def assess_series(
    months: list[date],
    values: list[Decimal | float | None],
    revenues: list[Decimal],
    *,
    scope: str,
    metric: str,
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Score each month from prior same-season values and earlier residuals only."""
    if len(months) != len(values) or len(months) != len(revenues):
        raise ValueError("anomaly series lengths differ")
    if metric not in {"net_revenue_eur", "gross_margin_rate"}:
        raise ValueError("unapproved anomaly metric")
    assessments: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    past_residuals: list[float] = []
    previous_sign = 0
    run = 0
    for index, (month, observed, revenue) in enumerate(zip(months, values, revenues, strict=True)):
        if month > cutoff:
            raise ValueError("post-cutoff anomaly actual")
        record: dict[str, Any] = {
            "scope": scope,
            "metric": metric,
            "observation_month": month,
            "observed_value": observed,
            "history_months": index,
            "method_version": METHOD_VERSION,
            "as_of_date": cutoff,
            "status": "insufficient_history",
            "evidence_quality": "low",
        }
        if observed is None or (metric == "gross_margin_rate" and revenue <= 0):
            record.update(status="invalid_denominator", evidence_quality="low")
            assessments.append(record)
            previous_sign = run = 0
            continue
        same_season = [
            float(values[index - 12 * lag])
            for lag in range(1, index // 12 + 1)
            if values[index - 12 * lag] is not None
        ]
        if len(same_season) < 2:
            assessments.append(record)
            previous_sign = run = 0
            continue
        expected = float(np.median(same_season))
        residual = float(observed) - expected
        record.update(
            expected_value=cents(expected) if metric == "net_revenue_eur" else expected,
            deviation=cents(residual) if metric == "net_revenue_eur" else residual,
            seasonal_comparisons=len(same_season),
        )
        prior = past_residuals.copy()
        past_residuals.append(residual)
        if len(prior) < MIN_RESIDUAL_HISTORY:
            assessments.append(record)
            previous_sign = run = 0
            continue
        centre = float(np.median(prior))
        mad = float(np.median(np.abs(np.asarray(prior) - centre)))
        if mad <= 1e-12:
            record.update(status="zero_mad", evidence_quality="low_zero_mad")
            assessments.append(record)
            previous_sign = run = 0
            continue
        score = (residual - centre) / (ROBUST_SCALE * mad)
        exposure = abs(residual) if metric == "net_revenue_eur" else abs(residual) * float(revenue)
        minimum_exposure = 1000.0 if metric == "net_revenue_eur" else 300.0
        minimum_change = 0.0 if metric == "net_revenue_eur" else 0.03
        qualifying = (
            abs(score) >= SCORE_THRESHOLD
            and exposure >= minimum_exposure
            and abs(residual) >= minimum_change
        )
        sign = int(np.sign(score)) if qualifying else 0
        run = run + 1 if sign and sign == previous_sign else 1 if sign else 0
        previous_sign = sign
        record.update(
            robust_score=float(score),
            exposure_eur=cents(exposure),
            residual_history_count=len(prior),
            persistence_months=run,
            status="anomaly" if qualifying else "within_expected_range",
            evidence_quality="high" if len(prior) >= 24 else "moderate",
        )
        if qualifying:
            if abs(score) >= 7 and exposure >= 20_000 and record["evidence_quality"] == "high":
                severity = "high"
            elif (abs(score) >= 5 and exposure >= 5_000) or run >= 3:
                severity = "investigate"
            else:
                severity = "info"
            record["severity"] = severity
            record["pattern"] = "persistent_shift" if run >= 3 else "isolated_spike"
            record["recommended_investigation"] = _recommendation(metric)
            anomalies.append(record.copy())
        assessments.append(record)
    return assessments, anomalies
