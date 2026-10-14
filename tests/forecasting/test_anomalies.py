"""Causal seasonal anomaly rules on small synthetic monthly series."""

from datetime import date
from decimal import Decimal

from baltic_sales_intelligence.anomalies.core import assess_series
from baltic_sales_intelligence.forecasting.core import add_months

MONTHS = [add_months(date(2022, 1, 1), index) for index in range(48)]
CUTOFF = date(2025, 12, 31)


def test_seasonal_holiday_pattern_and_sales_spike() -> None:
    values = [
        Decimal(10_000 + 5 * (index % 7) + (3000 if index % 12 == 11 else 0)) for index in range(48)
    ]
    values[45] += 20_000
    records, anomalies = assess_series(
        MONTHS, values, values, scope="LT", metric="net_revenue_eur", cutoff=CUTOFF
    )
    assert records[23]["status"] == "insufficient_history"
    assert records[47]["status"] != "anomaly"  # recurring December is seasonal
    assert records[45]["expected_value"] < records[45]["observed_value"]
    assert any(row["observation_month"] == MONTHS[45] for row in anomalies)
    assert all(row["severity"] in {"info", "investigate", "high"} for row in anomalies)


def test_zero_mad_and_sparse_margin_exposure() -> None:
    constant = [Decimal(10_000)] * 48
    records, anomalies = assess_series(
        MONTHS, constant, constant, scope="LV", metric="net_revenue_eur", cutoff=CUTOFF
    )
    assert records[36]["status"] == "zero_mad"
    assert not anomalies
    margins = [0.20 + 0.001 * (index % 7) for index in range(48)]
    margins[45] = -0.5
    low_revenue = [Decimal(100)] * 48
    assessed, flagged = assess_series(
        MONTHS, margins, low_revenue, scope="LV", metric="gross_margin_rate", cutoff=CUTOFF
    )
    assert assessed[45]["status"] == "within_expected_range"
    assert not flagged
    low_revenue[46] = Decimal(-1)
    assessed, _ = assess_series(
        MONTHS, margins, low_revenue, scope="LV", metric="gross_margin_rate", cutoff=CUTOFF
    )
    assert assessed[46]["status"] == "invalid_denominator"


def test_persistent_margin_deterioration() -> None:
    margins = [0.22 + 0.001 * (index % 7) for index in range(48)]
    margins[43:46] = [0.02] * 3
    revenue = [Decimal(100_000)] * 48
    records, anomalies = assess_series(
        MONTHS, margins, revenue, scope="EE", metric="gross_margin_rate", cutoff=CUTOFF
    )
    hits = [row for row in anomalies if row["observation_month"] in MONTHS[43:46]]
    assert len(hits) == 3
    assert hits[-1]["persistence_months"] == 3
    assert hits[-1]["pattern"] == "persistent_shift"
    assert hits[-1]["severity"] in {"investigate", "high"}
    assert records[45]["recommended_investigation"]
