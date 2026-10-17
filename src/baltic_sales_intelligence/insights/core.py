"""Seven deterministic SPEC-14 management rule families."""

# ruff: noqa: E501  # Trigger conditions and limitations are published verbatim.

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from itertools import pairwise
from typing import Any

from baltic_sales_intelligence.opportunities.policy import OpportunityPolicy


def _f(value: Any) -> float:
    return float(value or 0)


def _emit(
    rule: str,
    scope: str,
    as_of: date,
    start: date,
    *,
    supported: bool,
    triggered: bool,
    severity: str,
    numerator: float | None,
    denominator: float | None,
    threshold: float,
    condition: str,
    support_count: int,
    metrics: list[str],
    evidence: dict,
    interpretation: str,
    followup: str,
    limitation: str,
    policy: OpportunityPolicy,
) -> dict:
    status = (
        "insufficient_evidence"
        if not supported
        else ("triggered" if triggered else "not_triggered")
    )
    return {
        "insight_id": f"{rule}:{scope}:{as_of.isoformat()}",
        "rule_id": rule,
        "scope_code": scope,
        "as_of_date": as_of,
        "observation_start_date": start,
        "observation_end_date": as_of,
        "status": status,
        "severity": severity if triggered and supported else None,
        "numerator": numerator if supported else None,
        "denominator": denominator if supported else None,
        "threshold": threshold,
        "trigger_condition": condition,
        "support_count": support_count,
        "evidence_metric_ids": metrics,
        "independent_evidence": json.dumps(evidence, sort_keys=True, default=str),
        "management_interpretation": interpretation if triggered and supported else None,
        "recommended_next_step": followup if triggered and supported else None,
        "limitation": limitation,
        "method_version": policy.method_version,
    }


def _period(date_value: date, recent_start: date, prior_start: date, as_of: date) -> str | None:
    if date_value > as_of:
        return None
    if recent_start <= date_value:
        return "recent"
    if prior_start <= date_value < recent_start:
        return "prior"
    return None


def evaluate_insights(facts: dict[str, list[dict]], policy: OpportunityPolicy, as_of: date):
    """Evaluate every rule on complete comparable periods; no planted labels."""
    window = facts["windows"][0]
    recent_start, prior_start = window["recent_start"], window["prior_start"]
    out: list[dict] = []

    # R01: same-scope comparable-year revenue increase with GP margin decrease.
    monthly: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"revenue": 0, "gp": 0, "postings": 0, "months": set()}
    )
    for x in facts["monthly"]:
        period = _period(x["reporting_month"], recent_start, prior_start, as_of)
        if period:
            item = monthly[(x["scope"], period)]
            item["revenue"] += _f(x["revenue"])
            item["gp"] += _f(x["gp"])
            item["postings"] += x["posting_count"]
            item["months"].add(x["reporting_month"])
    for scope in ("LT", "LV", "EE", "COMPANY"):
        old, new = monthly[(scope, "prior")], monthly[(scope, "recent")]
        supported = (
            len(old["months"]) == len(new["months"]) == policy.observation_months
            and min(old["postings"], new["postings"]) >= policy.minimum_insight_postings
            and min(old["revenue"], new["revenue"]) > 0
        )
        old_margin = old["gp"] / old["revenue"] if old["revenue"] > 0 else None
        new_margin = new["gp"] / new["revenue"] if new["revenue"] > 0 else None
        growth = new["revenue"] / old["revenue"] - 1 if old["revenue"] > 0 else None
        trigger = (
            supported
            and growth >= policy.revenue_growth_threshold
            and old_margin - new_margin >= policy.margin_decline_points
        )
        out.append(
            _emit(
                "R01",
                scope,
                as_of,
                prior_start,
                supported=supported,
                triggered=trigger,
                severity="investigate",
                numerator=new["gp"],
                denominator=new["revenue"],
                threshold=policy.margin_decline_points,
                condition="recent revenue growth >= configured minimum AND prior GP/revenue minus recent GP/revenue >= threshold",
                support_count=min(old["postings"], new["postings"]),
                metrics=["FIN-K03", "FIN-K05", "FIN-K06"],
                evidence={
                    "revenue_growth": growth,
                    "prior_margin": old_margin,
                    "recent_margin": new_margin,
                    "recent_gp_eur": new["gp"],
                    "prior_gp_eur": old["gp"],
                },
                interpretation="Revenue grew while gross-profit margin declined; mix, price and costs warrant review.",
                followup="Review SKU mix, discount and cost changes on comparable periods.",
                limitation="Co-movement does not identify a causal margin driver; net revenue includes return timing.",
                policy=policy,
            )
        )

    # R02: event-period accepted defect share; deliberately not a mature cohort rate.
    buckets = {"prior": [], "recent": []}
    for x in facts["returns"]:
        period = _period(x["return_decision_date"], recent_start, prior_start, as_of)
        if period:
            buckets[period].append(x)
    prior, recent = buckets["prior"], buckets["recent"]
    old_defect = [x for x in prior if x["reason_group"] == "product_quality"]
    new_defect = [x for x in recent if x["reason_group"] == "product_quality"]
    supported = min(len(prior), len(recent)) >= policy.minimum_insight_postings
    old_share = len(old_defect) / len(prior) if prior else None
    new_share = len(new_defect) / len(recent) if recent else None
    loss = sum(
        max(0, _f(x["posted_refund_ex_vat_eur"]) - _f(x["posted_recovered_cogs_eur"]))
        for x in new_defect
    )
    out.append(
        _emit(
            "R02",
            "COMPANY",
            as_of,
            prior_start,
            supported=supported,
            triggered=(
                supported
                and new_share - old_share >= policy.return_quality_increase_points
                and loss > 0
            ),
            severity="investigate",
            numerator=len(new_defect),
            denominator=len(recent),
            threshold=policy.return_quality_increase_points,
            condition="recent defect-related accepted events / all accepted events exceeds prior share by threshold and GP reversal > 0",
            support_count=min(len(prior), len(recent)),
            metrics=["mart_product_return_event.reason_group", "FIN-K05"],
            evidence={
                "prior_defect_share": old_share,
                "recent_defect_share": new_share,
                "recent_defect_gp_reversal_eur": loss,
            },
            interpretation="Defect reasons form a larger share of accepted returns and carry observed GP reversal.",
            followup="Review quality records and return classifications by SKU.",
            limitation="Decision-period return-event share is not a mature fulfilled-unit return rate; reporting mix can change.",
            policy=policy,
        )
    )

    # R03: top-five positive customer net-revenue share crosses the same threshold.
    balances: dict[tuple[str, str, str], float] = defaultdict(float)
    for x in facts["customer_month"]:
        period = _period(x["reporting_month"], recent_start, prior_start, as_of)
        if period:
            balances[("COMPANY", period, x["customer_id"])] += _f(x["revenue"])
            balances[(x["country"], period, x["customer_id"])] += _f(x["revenue"])
    for scope in ("LT", "LV", "EE", "COMPANY"):
        values = {}
        for period in ("prior", "recent"):
            positive = sorted(
                (v for (s, p, c), v in balances.items() if s == scope and p == period and v > 0),
                reverse=True,
            )
            total_positive = sum(positive)
            values[period] = (
                sum(positive[:5]) / total_positive if positive else None,
                len(positive),
                total_positive,
                sum((v / total_positive) ** 2 for v in positive) if positive else None,
            )
        old, new = values["prior"], values["recent"]
        supported = (
            min(old[1], new[1]) >= policy.minimum_concentration_customers
            and min(old[2], new[2]) > 0
        )
        trigger = supported and old[0] < policy.concentration_top5_threshold <= new[0]
        out.append(
            _emit(
                "R03",
                scope,
                as_of,
                prior_start,
                supported=supported,
                triggered=trigger,
                severity="investigate",
                numerator=new[0] * new[2] if new[0] is not None else None,
                denominator=new[2],
                threshold=policy.concentration_top5_threshold,
                condition="prior top-five positive-customer net-revenue share < threshold <= recent share",
                support_count=min(old[1], new[1]),
                metrics=["FIN-K13", "mart_commercial_month.net_revenue_eur"],
                evidence={
                    "prior_top5_share": old[0],
                    "recent_top5_share": new[0],
                    "prior_positive_balance_hhi": old[3],
                    "recent_positive_balance_hhi": new[3],
                    "positive_customer_count": new[1],
                },
                interpretation="A larger share of positive customer revenue rests with five accounts.",
                followup="Review account dependency and retention plans.",
                limitation="Positive-balance denominator excludes zero/negative customer balances; this is a rolling-period view.",
                policy=policy,
            )
        )

    # R04: age-six retention compared across non-overlapping cohort vintages.
    mature = [
        x for x in facts["cohorts"] if x["is_observation_mature"] and x["cohort_month"] <= as_of
    ]
    months = sorted({x["cohort_month"] for x in mature})
    span = policy.cohort_comparison_months
    prior_months, recent_months = months[-2 * span : -span], months[-span:]
    selected_months = prior_months + recent_months
    contiguous = len(selected_months) == 2 * span and all(
        (right.year * 12 + right.month) - (left.year * 12 + left.month) == 1
        for left, right in pairwise(selected_months)
    )
    prior_set, recent_set = set(prior_months), set(recent_months)
    prior_cohorts = [x for x in mature if x["cohort_month"] in prior_set]
    recent_cohorts = [x for x in mature if x["cohort_month"] in recent_set]

    def cohort_rate(group):
        eligible = sum(x["eligible_customer_count"] for x in group)
        return (
            sum(x["retained_customer_count"] for x in group) / eligible if eligible else None,
            eligible,
            len({x["cohort_month"] for x in group}),
        )

    old, new = cohort_rate(prior_cohorts), cohort_rate(recent_cohorts)
    supported = (
        contiguous
        and min(old[1], new[1]) >= policy.minimum_cohort_size
        and min(old[2], new[2]) >= policy.minimum_cohort_groups
    )
    out.append(
        _emit(
            "R04",
            "COMPANY",
            as_of,
            prior_months[0] if prior_months else prior_start,
            supported=supported,
            triggered=supported and old[0] - new[0] >= policy.retention_decline_threshold,
            severity="investigate",
            numerator=sum(x["retained_customer_count"] for x in recent_cohorts),
            denominator=new[1],
            threshold=policy.retention_decline_threshold,
            condition="prior cohort-vintage age-six retained/eligible minus recent vintage age-six retained/eligible >= threshold",
            support_count=min(old[1], new[1]),
            metrics=["mart_cohort_retention.retention_rate"],
            evidence={
                "prior_age6_rate": old[0],
                "recent_age6_rate": new[0],
                "prior_cohorts": old[2],
                "recent_cohorts": new[2],
                "prior_first_cohort_month": prior_months[0] if prior_months else None,
                "recent_first_cohort_month": recent_months[0] if recent_months else None,
            },
            interpretation="Comparable age-six cohort retention declined across vintages.",
            followup="Examine cohort mix and account-level repeat purchasing.",
            limitation="Cohort composition and exposure differ; retention association is not a proven mechanism.",
            policy=policy,
        )
    )

    # R05: same-SKU, prior-list-weighted change removes product mix effects.
    matched = [
        x
        for x in facts["products"]
        if x["recent_units"] > 0
        and x["prior_units"] > 0
        and x["recent_list"] > 0
        and x["prior_list"] > 0
    ]
    weighted_gap = (
        sum(
            _f(x["prior_list"])
            * (
                _f(x["recent_discount"]) / _f(x["recent_list"])
                - _f(x["prior_discount"]) / _f(x["prior_list"])
            )
            for x in matched
        )
        / sum(_f(x["prior_list"]) for x in matched)
        if matched
        else None
    )
    squeezed = sum(
        (_f(x["recent_gp"]) / x["recent_units"] < _f(x["prior_gp"]) / x["prior_units"])
        for x in matched
    )
    supported = len(matched) >= policy.minimum_discount_products
    out.append(
        _emit(
            "R05",
            "COMPANY",
            as_of,
            prior_start,
            supported=supported,
            triggered=supported
            and weighted_gap >= policy.adjusted_discount_increase_points
            and squeezed > 0,
            severity="investigate",
            numerator=sum(_f(x["recent_discount"]) for x in matched),
            denominator=sum(_f(x["recent_list"]) for x in matched),
            threshold=policy.adjusted_discount_increase_points,
            condition="same-SKU prior-list-weighted discount-rate increase >= threshold and at least one matched SKU has lower unit GP",
            support_count=len(matched),
            metrics=["FIN-K07", "fact_order_line.posted_gross_profit_eur"],
            evidence={
                "mix_adjusted_discount_gap": weighted_gap,
                "matched_skus": len(matched),
                "lower_unit_gp_skus": squeezed,
            },
            interpretation="Discount intensity rose on matched SKUs alongside weaker unit contribution.",
            followup="Review contract exceptions and historical product cost snapshots.",
            limitation="Prior-list weighting controls SKU mix, not customer or channel mix; discount and cost causes remain open.",
            policy=policy,
        )
    )

    # R06: customer-home market decline alongside a channel-share shift.
    channel: dict[tuple[str, str, str], float] = defaultdict(float)
    for x in facts["channel_month"]:
        period = _period(x["reporting_month"], recent_start, prior_start, as_of)
        if period:
            channel[(x["country"], period, x["channel_code"])] += _f(x["revenue"])
    for scope in ("LT", "LV", "EE"):
        old_total = monthly[(scope, "prior")]["revenue"]
        new_total = monthly[(scope, "recent")]["revenue"]
        codes = {code for s, p, code in channel if s == scope}
        shifts = (
            {
                code: (
                    channel[(scope, "recent", code)] / new_total
                    - channel[(scope, "prior", code)] / old_total
                )
                for code in codes
            }
            if min(old_total, new_total) > 0
            else {}
        )
        largest = max((abs(v) for v in shifts.values()), default=0)
        decline = 1 - new_total / old_total if old_total > 0 else None
        supported = (
            min(old_total, new_total) > 0
            and len(codes) >= 2
            and min(monthly[(scope, "prior")]["postings"], monthly[(scope, "recent")]["postings"])
            >= policy.minimum_insight_postings
        )
        out.append(
            _emit(
                "R06",
                scope,
                as_of,
                prior_start,
                supported=supported,
                triggered=supported
                and decline >= policy.regional_revenue_decline
                and largest >= policy.regional_channel_mix_shift,
                severity="investigate",
                numerator=new_total,
                denominator=old_total,
                threshold=policy.regional_channel_mix_shift,
                condition="home-country revenue YoY decline >= configured threshold and largest channel revenue-share shift >= threshold",
                support_count=min(
                    monthly[(scope, "prior")]["postings"], monthly[(scope, "recent")]["postings"]
                ),
                metrics=["FIN-K03", "mart_commercial_month.channel_code"],
                evidence={
                    "revenue_decline": decline,
                    "largest_channel_share_shift": largest,
                    "channel_share_shifts": shifts,
                },
                interpretation="Home-market revenue fell as channel composition changed.",
                followup="Compare channel demand, account mix and fulfilment patterns.",
                limitation="Customer-home market and channel are distinct views; share movement does not explain revenue decline.",
                policy=policy,
            )
        )

    # R07: latest actual outside its historical 95% interval, with observed coverage disclosed.
    seen_forecast = set()
    for x in facts["forecast"]:
        seen_forecast.add(x["scope"])
        supported = (
            x["interval_status"] == "calibrated"
            and x["calibration_n"] >= 8
            and x["interval_n"] >= policy.minimum_forecast_evaluations
            and x["interval95_low_eur"] is not None
            and x["interval95_high_eur"] is not None
        )
        outside = supported and not (
            x["interval95_low_eur"] <= x["actual_eur"] <= x["interval95_high_eur"]
        )
        reliable = (
            x["coverage95"] is not None
            and x["coverage95"] >= policy.forecast_min_coverage95_for_high_severity
        )
        out.append(
            _emit(
                "R07",
                x["scope"],
                as_of,
                x["origin_month"],
                supported=supported,
                triggered=outside,
                severity="high" if reliable else "investigate",
                numerator=_f(x["error_eur"]),
                denominator=_f(x["interval95_high_eur"] - x["predicted_eur"])
                if x["interval95_high_eur"] is not None
                else None,
                threshold=1.0,
                condition="latest complete-month actual outside its origin's historically calibrated 95% interval",
                support_count=x["interval_n"],
                metrics=["walkforward_prediction.error_eur", "model_evaluation.coverage95"],
                evidence={
                    "prediction_eur": x["predicted_eur"],
                    "actual_eur": x["actual_eur"],
                    "interval95_low_eur": x["interval95_low_eur"],
                    "interval95_high_eur": x["interval95_high_eur"],
                    "observed_coverage95": x["coverage95"],
                    "calibration_n": x["calibration_n"],
                },
                interpretation="Observed revenue lies beyond a calibrated historical error band.",
                followup="Review recent postings and model bias; compare repeated errors over time.",
                limitation="Nominal 95% is not a probability guarantee; historical coverage is low in Latvia and horizons overlap.",
                policy=policy,
            )
        )
    for scope in {"LT", "LV", "EE", "COMPANY"} - seen_forecast:
        out.append(
            _emit(
                "R07",
                scope,
                as_of,
                date(as_of.year, as_of.month, 1),
                supported=False,
                triggered=False,
                severity="investigate",
                numerator=None,
                denominator=None,
                threshold=1.0,
                condition="latest complete-month actual outside its origin's historically calibrated 95% interval",
                support_count=0,
                metrics=["walkforward_prediction.error_eur", "model_evaluation.coverage95"],
                evidence={"reason": "no_eligible_latest_forecast"},
                interpretation="Observed revenue lies beyond a calibrated historical error band.",
                followup="Review recent postings and model bias; compare repeated errors over time.",
                limitation="No eligible latest historical forecast; nominal coverage is not a probability guarantee.",
                policy=policy,
            )
        )
    return sorted(out, key=lambda r: (r["rule_id"], r["scope_code"], r["insight_id"]))
