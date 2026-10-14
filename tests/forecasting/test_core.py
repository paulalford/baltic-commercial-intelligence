"""Small, independently checkable forecasting fixtures."""

from datetime import date
from decimal import Decimal

import pytest

from baltic_sales_intelligence.forecasting import core


def months(count: int) -> list[date]:
    return [core.add_months(date(2022, 1, 1), index) for index in range(count)]


def test_seasonal_naive_horizons_and_early_fallback() -> None:
    data = [Decimal(index) for index in range(1, 25)]
    assert core.baseline(data, 6) == ("seasonal_naive", [Decimal(index) for index in range(13, 19)])
    assert core.baseline(data[:11], 6) == ("trailing_three_mean", [Decimal(10)] * 6)
    assert [core.baseline(data, horizon)[1][-1] for horizon in core.HORIZONS] == [
        Decimal(13),
        Decimal(15),
        Decimal(18),
    ]


def test_statistical_candidate_eligibility_and_invalid_training_input() -> None:
    history = [Decimal(index) for index in range(23)]
    for model, minimum in (("ets_level", 24), ("ets_additive", 36), ("sarima_fixed", 48)):
        status, prediction, reason = core.model_forecast(model, history, 6)
        assert (status, prediction, reason) == (
            "insufficient_history",
            None,
            f"requires_{minimum}_months",
        )
    invalid = [Decimal("NaN"), *([Decimal(1)] * 23)]
    assert core.model_forecast("ets_level", invalid, 6) == (
        "invalid_input",
        None,
        "nonfinite_training_value",
    )


def test_expanding_origins_and_no_future_actuals(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[Decimal]] = []

    def fake_model(model: str, history: list[Decimal], steps: int):
        calls.append(history.copy())
        return "passed", [history[-1]] * steps, None

    monkeypatch.setattr(core, "model_forecast", fake_model)
    actual = [Decimal(index) for index in range(30)]
    predictions, diagnostics = core.backtest_series(months(30), actual, "LT")
    assert len(diagnostics) == 6 * len(core.MODELS)
    assert {len(call) for call in calls} == set(range(24, 30))
    assert all(call == actual[: len(call)] for call in calls)
    assert {row["horizon"] for row in predictions} == {1, 3, 6}
    assert all(
        row["target_month"] == core.add_months(row["origin_month"], row["horizon"])
        for row in predictions
    )
    with pytest.raises(ValueError, match="cadence gap"):
        core.backtest_series([*months(29), date(2024, 7, 1)], actual, "LT")


def test_model_selection_uses_matured_targets_and_baseline_guardrail() -> None:
    rows = []
    for index in range(10):
        origin = core.add_months(date(2022, 1, 1), index)
        target = core.add_months(origin, 3)
        for model, prediction in (("seasonal_naive", 80), ("ets_level", 98), ("ets_additive", 99)):
            rows.append(
                dict(
                    scope="LT",
                    origin_month=origin,
                    target_month=target,
                    horizon=3,
                    model=model,
                    actual_eur=Decimal(100),
                    error_eur=Decimal(100 - prediction),
                )
            )
    early = core.choose_model(rows, "LT", 3, date(2022, 10, 1))
    late = core.choose_model(rows, "LT", 3, date(2023, 1, 1))
    assert early["selected_model"] == "seasonal_naive"  # only seven targets matured
    assert late["selected_model"] == "ets_level"
    assert late["paired_origins"] == 10


def test_metrics_zero_denominators_and_known_errors() -> None:
    fixture = [
        dict(
            scope="LT",
            horizon=1,
            model="seasonal_naive",
            origin_month=months(2)[i],
            actual_eur=Decimal(x),
            predicted_eur=Decimal(y),
            error_eur=Decimal(x - y),
        )
        for i, (x, y) in enumerate(((0, 0), (100, 80)))
    ]
    result = core.evaluation_metrics(fixture)[0]
    assert result["wape"] == pytest.approx(0.2)
    assert result["mae_eur"] == 10
    assert result["rmse_eur"] == pytest.approx(200**0.5)
    assert result["mean_error_eur"] == 10
    assert result["smape"] == pytest.approx(1 / 9)
    zero = core.evaluation_metrics([fixture[0]])[0]
    assert zero["wape"] is None
    assert zero["smape"] == 0


def test_calibration_uses_only_matured_errors_and_nests() -> None:
    rows = []
    for index in range(15):
        origin = core.add_months(date(2022, 1, 1), index)
        rows.append(
            dict(
                scope="LT",
                horizon=3,
                model="selected",
                origin_month=origin,
                target_month=core.add_months(origin, 3),
                predicted_eur=Decimal(100),
                actual_eur=Decimal(100 + index),
                error_eur=Decimal(index),
            )
        )
    core.calibrate_backtest(rows)
    assert rows[9]["interval_status"] == "insufficient_history"
    assert rows[10]["interval_status"] == "calibrated"
    assert rows[10]["calibration_n"] == 8
    assert rows[10]["interval95_low_eur"] <= rows[10]["interval80_low_eur"]
    assert rows[10]["interval95_high_eur"] >= rows[10]["interval80_high_eur"]
    final = core.final_interval(rows, "LT", 3, "selected", rows[-1]["target_month"], Decimal(-5))
    assert final["interval80_low_eur"] < 0  # negative predictions are preserved


def test_bottom_up_prequential_coherence() -> None:
    candidate = []
    for scope, actual, predicted in (
        ("LT", 10, 9),
        ("LV", 20, 19),
        ("EE", 30, 29),
        ("COMPANY", 60, 99),
    ):
        candidate.append(
            dict(
                scope=scope,
                origin_month=date(2024, 1, 1),
                target_month=date(2024, 2, 1),
                horizon=1,
                model="seasonal_naive",
                source_model="seasonal_naive",
                training_months=24,
                actual_eur=Decimal(actual),
                predicted_eur=Decimal(predicted),
                error_eur=Decimal(actual - predicted),
            )
        )
    selected, _ = core.select_prequential(candidate, ("LT", "LV", "EE", "COMPANY"))
    company = next(row for row in selected if row["model"] == "reconciled")
    assert company["predicted_eur"] == Decimal(57)
    assert company["coherence_delta_eur"] == Decimal(42)
    assert company["error_eur"] == Decimal(3)


def test_final_joint_company_interval_and_deterministic_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fixed_forecast(model: str, history: list[Decimal], steps: int):
        if model != "seasonal_naive":
            return "insufficient_history", None, "miniature_fixture"
        return "passed", [history[-1]] * steps, "seasonal_naive"

    monkeypatch.setattr(core, "model_forecast", fixed_forecast)
    dates = months(57)
    series = {
        scope: [Decimal(value)] * 57
        for scope, value in (("LT", 10), ("LV", 20), ("EE", 30), ("COMPANY", 60))
    }
    historical = []
    for horizon in core.HORIZONS:
        for index in range(10):
            origin = dates[30 + index]
            target = core.add_months(origin, horizon)
            for scope, error in (("LT", 1), ("LV", -1), ("EE", 2), ("COMPANY", 2)):
                historical.append(
                    dict(
                        scope=scope,
                        origin_month=origin,
                        target_month=target,
                        horizon=horizon,
                        model="selected" if scope != "COMPANY" else "reconciled",
                        error_eur=Decimal(error),
                    )
                )
    first, decisions, diagnostics = core.final_forecasts(dates, series, [], historical)
    second, _, _ = core.final_forecasts(dates, series, [], historical)
    assert first == second
    assert len(first) == len(decisions) == 12
    assert len(diagnostics) == 4 * len(core.MODELS)
    for horizon in core.HORIZONS:
        company = next(
            row for row in first if row["scope"] == "COMPANY" and row["horizon"] == horizon
        )
        countries = [
            row for row in first if row["scope"] != "COMPANY" and row["horizon"] == horizon
        ]
        assert company["predicted_eur"] == sum(
            (row["predicted_eur"] for row in countries), Decimal(0)
        )
        assert company["interval_status"] == "calibrated"
        assert company["calibration_n"] == 10
        # Joint company errors of 2 EUR give a 2 EUR radius, not the 4 EUR
        # sum of the three marginal absolute-error radii.
        assert company["interval80_high_eur"] - company["predicted_eur"] == Decimal(2)
