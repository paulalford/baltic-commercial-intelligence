"""Pure monthly forecasts, prequential selection, metrics, and interval calibration."""

from __future__ import annotations

import warnings
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import numpy as np
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

CENT = Decimal("0.01")
HORIZONS = (1, 3, 6)
MODELS = ("seasonal_naive", "ets_level", "ets_additive", "sarima_fixed")
COMPLEXITY = {name: rank for rank, name in enumerate(MODELS)}
MIN_TRAIN = 24
MIN_CALIBRATION = 8
MIN_PAIRED = 8
MIN_RELATIVE_WAPE_GAIN = 0.05


def cents(value: Decimal | float | int) -> Decimal:
    """Quantize predictions to posted cents without changing source actuals."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def add_months(month: date, count: int) -> date:
    ordinal = month.year * 12 + month.month - 1 + count
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def baseline(history: list[Decimal], steps: int) -> tuple[str, list[Decimal]]:
    """Multi-step lag-12 seasonal naive; early windows use a fixed trailing-3 mean."""
    if steps < 1 or steps > 12 or len(history) < 3:
        raise ValueError("baseline requires three observations and 1..12 steps")
    if len(history) >= 12:
        return "seasonal_naive", [history[-12 + step] for step in range(steps)]
    mean = cents(sum(history[-3:], Decimal(0)) / 3)
    return "trailing_three_mean", [mean] * steps


def model_forecast(
    model: str, history: list[Decimal], steps: int
) -> tuple[str, list[Decimal] | None, str | None]:
    """Fit a prespecified candidate only on the supplied training window."""
    n = len(history)
    if model == "seasonal_naive":
        if n < 3:
            return "insufficient_history", None, "fewer_than_three_months"
        name, prediction = baseline(history, steps)
        return "passed", prediction, name
    minimum = {"ets_level": 24, "ets_additive": 36, "sarima_fixed": 48}.get(model)
    if minimum is None:
        raise ValueError(f"unknown model: {model}")
    if n < minimum:
        return "insufficient_history", None, f"requires_{minimum}_months"
    values = np.asarray([float(x) for x in history], dtype=float)
    if not np.isfinite(values).all():
        return "invalid_input", None, "nonfinite_training_value"
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            if model == "ets_level":
                fitted = ExponentialSmoothing(
                    values, trend=None, seasonal=None, initialization_method="estimated"
                ).fit(optimized=True)
                result = fitted.forecast(steps)
            elif model == "ets_additive":
                fitted = ExponentialSmoothing(
                    values,
                    trend="add",
                    seasonal="add",
                    seasonal_periods=12,
                    initialization_method="estimated",
                ).fit(optimized=True)
                result = fitted.forecast(steps)
            else:
                fitted = SARIMAX(
                    values,
                    order=(0, 1, 1),
                    seasonal_order=(0, 1, 1, 12),
                    trend="n",
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                ).fit(disp=False, maxiter=100)
                result = fitted.get_forecast(steps=steps).predicted_mean
                if not fitted.mle_retvals.get("converged", False):
                    return "failed", None, "optimizer_not_converged"
            if any(issubclass(item.category, ConvergenceWarning) for item in caught):
                return "failed", None, "convergence_warning"
        prediction = np.asarray(result, dtype=float)
        if len(prediction) != steps or not np.isfinite(prediction).all():
            return "failed", None, "nonfinite_or_wrong_length_prediction"
        return "passed", [cents(x) for x in prediction], None
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        return "failed", None, f"{type(exc).__name__}:{str(exc)[:160]}"


def backtest_series(
    months: list[date], actuals: list[Decimal], scope: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Refit each fixed model monthly with at least 24 earlier months."""
    if len(months) != len(actuals) or len(set(months)) != len(months):
        raise ValueError("monthly training series has duplicate or mismatched months")
    if any(add_months(months[i], 1) != months[i + 1] for i in range(len(months) - 1)):
        raise ValueError("monthly training series has a cadence gap")
    predictions: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for origin_index in range(MIN_TRAIN - 1, len(months) - 1):
        origin = months[origin_index]
        training = actuals[: origin_index + 1]
        for model in MODELS:
            status, forecast, reason = model_forecast(model, training, 6)
            diagnostics.append(
                {
                    "scope": scope,
                    "origin_month": origin,
                    "model": model,
                    "training_months": len(training),
                    "status": status,
                    "reason": reason,
                }
            )
            if forecast is None:
                continue
            for horizon in HORIZONS:
                target_index = origin_index + horizon
                if target_index >= len(months):
                    continue
                actual = actuals[target_index]
                predicted = forecast[horizon - 1]
                predictions.append(
                    {
                        "scope": scope,
                        "origin_month": origin,
                        "target_month": months[target_index],
                        "horizon": horizon,
                        "model": model,
                        "source_model": model,
                        "training_months": len(training),
                        "actual_eur": actual,
                        "predicted_eur": predicted,
                        "error_eur": actual - predicted,
                    }
                )
    return predictions, diagnostics


def _wape(rows: list[dict[str, Any]]) -> float | None:
    denominator = sum((abs(r["actual_eur"]) for r in rows), Decimal(0))
    if denominator == 0:
        return None
    numerator = sum((abs(r["error_eur"]) for r in rows), Decimal(0))
    return float(numerator / denominator)


def choose_model(
    candidates: list[dict[str, Any]], scope: str, horizon: int, origin: date
) -> dict[str, Any]:
    """Use only errors whose target is observable by this origin; favour baseline."""
    past = [
        r
        for r in candidates
        if r["scope"] == scope and r["horizon"] == horizon and r["target_month"] <= origin
    ]
    baseline_rows = {r["origin_month"]: r for r in past if r["model"] == "seasonal_naive"}
    chosen = "seasonal_naive"
    best = None
    comparison_n = 0
    baseline_wape = None
    for model in MODELS[1:]:
        model_rows = {r["origin_month"]: r for r in past if r["model"] == model}
        common = sorted(baseline_rows.keys() & model_rows.keys())
        if len(common) < MIN_PAIRED:
            continue
        base = _wape([baseline_rows[x] for x in common])
        challenger = _wape([model_rows[x] for x in common])
        if base is None or base == 0 or challenger is None:
            continue
        if challenger <= base * (1 - MIN_RELATIVE_WAPE_GAIN):
            # Candidate histories differ by minimum fit length. Choose the
            # simplest model that clears its paired baseline guardrail rather
            # than ranking raw WAPE across incomparable historical windows.
            rank = (COMPLEXITY[model], challenger)
            if best is None or rank < best:
                best = rank
                chosen = model
                comparison_n = len(common)
                baseline_wape = base
    available = {
        r["model"]
        for r in candidates
        if r["scope"] == scope and r["horizon"] == horizon and r["origin_month"] == origin
    }
    if chosen not in available and origin in {r["origin_month"] for r in candidates}:
        chosen = "seasonal_naive"
        reason = "preferred_model_unavailable_at_origin"
    else:
        reason = "paired_wape_gain_at_least_5pct" if best is not None else "baseline_preferred"
    return {
        "scope": scope,
        "horizon": horizon,
        "origin_month": origin,
        "selected_model": chosen,
        "reason": reason,
        "paired_origins": comparison_n,
        "baseline_wape": baseline_wape,
        "selected_wape": best[1] if best else None,
    }


def select_prequential(
    candidates: list[dict[str, Any]], scopes: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create leakage-free selected and bottom-up company backtest rows."""
    by_key = {(r["scope"], r["origin_month"], r["horizon"], r["model"]): r for r in candidates}
    origins = sorted({r["origin_month"] for r in candidates})
    selected = []
    decisions = []
    for scope in scopes:
        for origin in origins:
            for horizon in HORIZONS:
                baseline_key = (scope, origin, horizon, "seasonal_naive")
                if baseline_key not in by_key:
                    continue
                decision = choose_model(candidates, scope, horizon, origin)
                decisions.append(decision)
                source = by_key[(scope, origin, horizon, decision["selected_model"])]
                selected.append({**source, "model": "selected", "source_model": source["model"]})
    selected_index = {(r["scope"], r["origin_month"], r["horizon"]): r for r in selected}
    reconciled = []
    for row in selected:
        if row["scope"] != "COMPANY":
            continue
        key = (row["origin_month"], row["horizon"])
        countries = [selected_index.get((country, *key)) for country in ("LT", "LV", "EE")]
        if any(country is None for country in countries):
            raise ValueError("country forecasts missing for bottom-up reconciliation")
        predicted = sum((country["predicted_eur"] for country in countries), Decimal(0))
        reconciled.append(
            {
                **row,
                "model": "reconciled",
                "source_model": "bottom_up_selected_countries",
                "predicted_eur": predicted,
                "error_eur": row["actual_eur"] - predicted,
                "coherence_delta_eur": row["predicted_eur"] - predicted,
            }
        )
    return selected + reconciled, decisions


def _radius(errors: list[Decimal], probability: float) -> Decimal:
    return cents(np.quantile([float(abs(x)) for x in errors], probability, method="higher"))


def calibrate_backtest(rows: list[dict[str, Any]]) -> None:
    """Attach nested absolute-residual intervals available before each origin in place."""
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["model"] in {"selected", "reconciled"}:
            groups[(row["scope"], row["horizon"], row["model"])].append(row)
    for group in groups.values():
        group.sort(key=lambda row: row["origin_month"])
        for row in group:
            errors = [
                past["error_eur"]
                for past in group
                if past["origin_month"] < row["origin_month"]
                and past["target_month"] <= row["origin_month"]
            ]
            row["calibration_n"] = len(errors)
            if len(errors) < MIN_CALIBRATION:
                row["interval_status"] = "insufficient_history"
                continue
            low80 = _radius(errors, 0.8)
            low95 = max(low80, _radius(errors, 0.95))
            row.update(
                interval_status="calibrated",
                interval80_low_eur=row["predicted_eur"] - low80,
                interval80_high_eur=row["predicted_eur"] + low80,
                interval95_low_eur=row["predicted_eur"] - low95,
                interval95_high_eur=row["predicted_eur"] + low95,
            )


def final_interval(
    history_rows: list[dict[str, Any]],
    scope: str,
    horizon: int,
    model: str,
    origin: date,
    prediction: Decimal,
) -> dict[str, Any]:
    errors = [
        row["error_eur"]
        for row in history_rows
        if row["scope"] == scope
        and row["horizon"] == horizon
        and row["model"] == model
        and row["target_month"] <= origin
    ]
    if len(errors) < MIN_CALIBRATION:
        return {"interval_status": "insufficient_history", "calibration_n": len(errors)}
    radius80 = _radius(errors, 0.8)
    radius95 = max(radius80, _radius(errors, 0.95))
    return {
        "interval_status": "calibrated",
        "calibration_n": len(errors),
        "interval80_low_eur": prediction - radius80,
        "interval80_high_eur": prediction + radius80,
        "interval95_low_eur": prediction - radius95,
        "interval95_high_eur": prediction + radius95,
    }


def evaluation_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scope"], row["horizon"], row["model"])].append(row)
    result = []
    for (scope, horizon, model), group in sorted(groups.items()):
        errors = np.asarray([float(x["error_eur"]) for x in group])
        actual = np.asarray([float(x["actual_eur"]) for x in group])
        predicted = np.asarray([float(x["predicted_eur"]) for x in group])
        denominator = np.abs(actual) + np.abs(predicted)
        smape = np.where(denominator > 0, 2 * np.abs(errors) / np.maximum(denominator, 1e-300), 0)
        calibrated = [x for x in group if x.get("interval_status") == "calibrated"]
        result.append(
            {
                "scope": scope,
                "horizon": horizon,
                "model": model,
                "n": len(group),
                "origin_first": min(x["origin_month"] for x in group),
                "origin_last": max(x["origin_month"] for x in group),
                "wape": _wape(group),
                "mae_eur": float(np.mean(np.abs(errors))),
                "rmse_eur": float(np.sqrt(np.mean(errors**2))),
                "mean_error_eur": float(np.mean(errors)),
                "smape": float(np.mean(smape)),
                "interval_n": len(calibrated),
                "coverage80": (
                    sum(
                        x["interval80_low_eur"] <= x["actual_eur"] <= x["interval80_high_eur"]
                        for x in calibrated
                    )
                    / len(calibrated)
                    if calibrated
                    else None
                ),
                "coverage95": (
                    sum(
                        x["interval95_low_eur"] <= x["actual_eur"] <= x["interval95_high_eur"]
                        for x in calibrated
                    )
                    / len(calibrated)
                    if calibrated
                    else None
                ),
                "mean_width80_eur": (
                    float(
                        np.mean(
                            [
                                float(x["interval80_high_eur"] - x["interval80_low_eur"])
                                for x in calibrated
                            ]
                        )
                    )
                    if calibrated
                    else None
                ),
                "mean_width95_eur": (
                    float(
                        np.mean(
                            [
                                float(x["interval95_high_eur"] - x["interval95_low_eur"])
                                for x in calibrated
                            ]
                        )
                    )
                    if calibrated
                    else None
                ),
            }
        )
    return result


def final_forecasts(
    months: list[date],
    series: dict[str, list[Decimal]],
    candidates: list[dict[str, Any]],
    prequential: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Select with fully observed holdouts, then reconcile future country forecasts."""
    origin = months[-1]
    fitted: dict[tuple[str, str], list[Decimal]] = {}
    diagnostics: list[dict[str, Any]] = []
    for scope in ("LT", "LV", "EE", "COMPANY"):
        for model in MODELS:
            status, prediction, reason = model_forecast(model, series[scope], 6)
            diagnostics.append(
                {
                    "scope": scope,
                    "origin_month": origin,
                    "model": model,
                    "training_months": len(months),
                    "status": status,
                    "reason": reason,
                }
            )
            if prediction is not None:
                fitted[(scope, model)] = prediction
    final: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    independent: dict[int, dict[str, Any]] = {}
    for scope in ("LT", "LV", "EE", "COMPANY"):
        for horizon in HORIZONS:
            decision = choose_model(candidates, scope, horizon, origin)
            chosen = decision["selected_model"]
            if (scope, chosen) not in fitted:
                chosen = "seasonal_naive"
                decision["selected_model"] = chosen
                decision["reason"] = "preferred_model_failed_full_history_fit"
            if (scope, chosen) not in fitted:
                raise ValueError("full-history seasonal baseline unavailable")
            prediction = fitted[(scope, chosen)][horizon - 1]
            row = {
                "scope": scope,
                "origin_month": origin,
                "target_month": add_months(origin, horizon),
                "horizon": horizon,
                "model": "selected" if scope != "COMPANY" else "independent_company",
                "source_model": chosen,
                "predicted_eur": prediction,
                "training_months": len(months),
            }
            if scope == "COMPANY":
                independent[horizon] = row
            else:
                row.update(
                    final_interval(prequential, scope, horizon, "selected", origin, prediction)
                )
                final.append(row)
            decisions.append(decision)
    for horizon in HORIZONS:
        country_rows = [r for r in final if r["horizon"] == horizon]
        if len(country_rows) != 3:
            raise ValueError("final country forecast incomplete")
        prediction = sum((r["predicted_eur"] for r in country_rows), Decimal(0))
        standalone = independent[horizon]["predicted_eur"]
        row = {
            "scope": "COMPANY",
            "origin_month": origin,
            "target_month": add_months(origin, horizon),
            "horizon": horizon,
            "model": "reconciled",
            "source_model": "bottom_up_selected_countries",
            "predicted_eur": prediction,
            "training_months": len(months),
            "independent_company_eur": standalone,
            "coherence_delta_eur": standalone - prediction,
        }
        row.update(
            final_interval(prequential, "COMPANY", horizon, "reconciled", origin, prediction)
        )
        final.append(row)
    return final, decisions, diagnostics
