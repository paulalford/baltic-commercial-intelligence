"""Miniature governed opportunity and management-rule fixtures."""

from __future__ import annotations

import copy
import json
from datetime import date
from decimal import Decimal

from baltic_sales_intelligence.insights.core import evaluate_insights
from baltic_sales_intelligence.opportunities.core import build_opportunities, scenario
from baltic_sales_intelligence.opportunities.policy import load_policy

AS_OF = date(2026, 9, 30)
D = Decimal


def policy():
    return load_policy("config").model_copy(
        update={
            "minimum_peer_accounts": 2,
            "minimum_product_units": 2,
            "minimum_defect_events": 4,
            "minimum_defect_quarter_events": 2,
            "minimum_insight_postings": 2,
            "minimum_concentration_customers": 2,
            "minimum_cohort_size": 2,
            "minimum_cohort_groups": 2,
            "cohort_comparison_months": 3,
            "minimum_discount_products": 1,
            "minimum_forecast_evaluations": 2,
        }
    )


def customer(cid, *, lapsed=False, recent=100, prior=80, lifetime=500):
    return dict(
        customer_id=cid,
        customer_name=f"Account {cid}",
        segment="S",
        country="LT",
        first_fulfilled_date=date(2023, 1, 1),
        last_fulfilled_date=date(2024, 1, 1) if lapsed else date(2026, 9, 1),
        lifetime_orders=4,
        lifetime_gp=D(lifetime),
        tenure_days=730,
        is_lapsed_buyer=lapsed,
        is_active_buyer=not lapsed,
        recency_days=600 if lapsed else 15,
        recent_gp=D(recent),
        prior_gp=D(prior),
        recent_revenue=D(recent * 3),
        prior_revenue=D(prior * 3),
    )


def event(product, when, defect=True, idx=0):
    return dict(
        product_id=product,
        category_id="CAT",
        return_decision_date=when,
        accepted_quantity=1,
        posted_refund_ex_vat_eur=D(30),
        posted_recovered_cogs_eur=D(10),
        reason_group="product_quality" if defect else "buyer_or_expectation",
        return_id=f"{product}-{when}-{idx}",
    )


def opportunity_facts():
    customers = [
        customer("C1", lapsed=True, recent=0, prior=0),
        customer("C2", recent=180, prior=100),
        customer("C3", recent=160, prior=100),
        customer("C4", recent=140, prior=100),
    ]
    subcats = [
        dict(
            customer_id="C2", subcategory_id="A", category_id="CAT-A", gp_eur=D(40), active_months=4
        ),
        dict(
            customer_id="C3", subcategory_id="B", category_id="CAT-B", gp_eur=D(80), active_months=4
        ),
        dict(
            customer_id="C4", subcategory_id="B", category_id="CAT-B", gp_eur=D(70), active_months=4
        ),
    ]
    products = [
        dict(
            product_id="P1",
            category_id="CAT",
            subcategory_id="B",
            recent_units=100,
            prior_units=100,
            recent_list=D(1000),
            prior_list=D(1000),
            recent_discount=D(100),
            prior_discount=D(50),
            recent_gp=D(200),
            prior_gp=D(300),
        )
    ]
    returns = [
        event("P1", date(2026, 5, 1), idx=1),
        event("P1", date(2026, 6, 1), idx=2),
        event("P1", date(2026, 8, 1), idx=3),
        event("P1", date(2026, 9, 1), idx=4),
        event("P2", date(2026, 5, 1), False),
        event("P2", date(2026, 8, 1), False),
    ]
    return dict(
        customers=customers,
        customer_subcategories=subcats,
        basket_pairs=[
            dict(
                subcategory_a_id="A",
                subcategory_b_id="B",
                basket_count=100,
                joint_baskets=30,
                pair_support=0.30,
                lift=2.0,
                q_value=0.01,
                status="association_only",
            )
        ],
        basket_context=[
            dict(
                country="LT",
                segment="S",
                subcategory_a_id="A",
                subcategory_b_id="B",
                joint_baskets=30,
                confidence_a_to_b=D("0.60"),
                confidence_b_to_a=D("0.20"),
                context_status="threshold_met",
            )
        ],
        products=products,
        returns=returns,
        windows=[
            dict(
                recent_start=date(2025, 10, 1),
                prior_start=date(2024, 10, 1),
                previous_quarter=date(2026, 4, 1),
                quarter_start=date(2026, 7, 1),
            )
        ],
    )


def test_six_families_financial_ordering_and_overlap() -> None:
    facts = opportunity_facts()
    rows, overlaps, suppressed = build_opportunities(facts, policy(), AS_OF)
    assert {r["opportunity_code"] for r in rows} == {f"O{i:02d}" for i in range(1, 7)}
    estimable = {r["opportunity_code"] for r in rows if r["status"] == "estimable"}
    assert estimable == {f"O{i:02d}" for i in range(1, 7)}
    bases = {r["opportunity_id"]: r["estimated_impact_base"] for r in rows}
    assert bases["O01:C1"] == D("32.00")
    assert bases["O02:C2:A:B"] == D("2.25")
    assert bases["O03:P1"] == D("10.00")
    assert bases["O04:C2:CAT-B"] == D("10.00")
    assert bases["O05:C2"] == D("10.00")
    assert bases["O06:P1"] == D("16.00")
    assert rows == build_opportunities(facts, policy(), AS_OF)[0]
    assert [r["opportunity_id"] for r in rows] == [
        r["opportunity_id"]
        for r in sorted(rows, key=lambda r: (-r["score"], r["opportunity_code"], r["entity_id"]))
    ]
    for row in rows:
        assert 0 <= row["score"] <= 100
        assert row["score"] == round(
            0.4 * row["impact_score"]
            + 0.3 * row["confidence_score"]
            + 0.2 * row["actionability_score"]
            + 0.1 * row["urgency_score"],
            2,
        )
        if row["status"] == "estimable":
            assert (
                D(0)
                <= row["estimated_impact_low"]
                <= row["estimated_impact_base"]
                <= row["estimated_impact_high"]
            )
            assert "gross profit" in row["impact_unit"]
        else:
            assert all(
                row[x] is None
                for x in ("estimated_impact_low", "estimated_impact_base", "estimated_impact_high")
            )
    assert any(r["opportunity_id"] == "O06:P1" and r["status"] == "estimable" for r in rows)
    assert any(item[2] == "product:P1" for item in overlaps)
    assert all(
        r["must_not_sum"]
        for r in rows
        if r["opportunity_id"] in {item[0] for item in overlaps} | {item[1] for item in overlaps}
    )
    assert (
        "O03:insufficient_same_sku_units" in suppressed or suppressed["O03:management_limit"] == 0
    )
    row = next(r for r in rows if r["opportunity_id"] == "O01:C1")
    assert row["problem_or_potential"] == "Lapsed account with historically positive contribution"
    narrower = policy().model_copy(update={"scenario_low": 0.2, "scenario_high": 0.2})
    narrow_rows = build_opportunities(facts, narrower, AS_OF)[0]
    narrow = next(r for r in narrow_rows if r["opportunity_id"] == "O01:C1")
    assert narrow["confidence_score"] > row["confidence_score"]


def test_unsupported_candidate_has_no_positive_financial_claim() -> None:
    facts = opportunity_facts()
    facts["basket_context"] = []
    rows, _, _ = build_opportunities(facts, policy(), AS_OF)
    cross_sell = next(r for r in rows if r["opportunity_code"] == "O02")
    assert cross_sell["status"] == "investigative"
    assert cross_sell["estimated_impact_base"] is None
    assert scenario(D(0), (0.1, 0.2, 0.35), D(100)) == (D("0.00"),) * 3


def test_cross_sell_directional_attachment_and_comparable_peer_contribution() -> None:
    facts = opportunity_facts()
    facts["customers"].append(customer("C5", recent=100))
    facts["customer_subcategories"].append(
        dict(customer_id="C5", subcategory_id="A", category_id="CAT-A", gp_eur=D(60))
    )
    rows, _, _ = build_opportunities(facts, policy(), AS_OF)
    forward = next(r for r in rows if r["opportunity_id"] == "O02:C2:A:B")
    reverse = next(r for r in rows if r["opportunity_id"] == "O02:C3:B:A")
    assert forward["estimated_impact_base"] == D("2.25")  # 75 * .60 * .05
    assert reverse["estimated_impact_base"] == D("0.50")  # 50 * .20 * .05
    assert forward["estimated_impact_low"] == D("0.90")
    assert forward["estimated_impact_high"] == D("4.50")
    assert json.loads(forward["baseline"])["observed_local_a_to_b_attachment"] == "0.60"
    assert "mart_basket_context.confidence_a_to_b" in forward["evidence_metric_ids"]
    assert "mart_basket_context.confidence_b_to_a" in reverse["evidence_metric_ids"]
    assert json.loads(forward["assumptions"])["attachment_scenario_factors"] == [0.02, 0.05, 0.10]
    assert (
        "joint/A basket attachment * conservative scenario factor" in forward["estimation_formula"]
    )
    capped = policy().model_copy(update={"annual_impact_cap_fraction": 0.01})
    capped_forward = next(
        r
        for r in build_opportunities(facts, capped, AS_OF)[0]
        if r["opportunity_id"] == "O02:C2:A:B"
    )
    assert capped_forward["estimated_impact_base"] == D("1.80")
    assert capped_forward["estimated_impact_high"] == D("1.80")

    outside = copy.deepcopy(facts)
    outside["customers"].append(customer("C6", recent=200))
    outside["customers"][-1]["country"] = "LV"
    outside["customer_subcategories"].append(
        dict(customer_id="C6", subcategory_id="B", category_id="CAT-B", gp_eur=D(10000))
    )
    other = next(
        r
        for r in build_opportunities(outside, policy(), AS_OF)[0]
        if r["opportunity_id"] == "O02:C2:A:B"
    )
    assert other["estimated_impact_base"] == forward["estimated_impact_base"]
    assert json.loads(other["baseline"])["comparable_positive_b_peer_accounts"] == 2

    sparse = copy.deepcopy(facts)
    sparse["customers"][3]["segment"] = "OTHER"
    row = next(
        r
        for r in build_opportunities(sparse, policy(), AS_OF)[0]
        if r["opportunity_id"] == "O02:C2:A:B"
    )
    assert row["status"] == "investigative"
    assert all(row[f"estimated_impact_{bound}"] is None for bound in ("low", "base", "high"))


def test_same_family_cross_sell_shared_b_base_is_flagged_once_per_identifier() -> None:
    facts = opportunity_facts()
    facts["customer_subcategories"].append(
        dict(customer_id="C2", subcategory_id="C", category_id="CAT-C", gp_eur=D(20))
    )
    pair = dict(facts["basket_pairs"][0])
    pair.update(subcategory_a_id="B", subcategory_b_id="C")
    facts["basket_pairs"].append(pair)
    context = dict(facts["basket_context"][0])
    context.update(subcategory_a_id="B", subcategory_b_id="C")
    facts["basket_context"].append(context)
    rows, overlaps, _ = build_opportunities(facts, policy(), AS_OF)
    ids = {"O02:C2:A:B", "O02:C2:C:B"}
    shared = [x for x in overlaps if {x[0], x[1]} == ids]
    assert {x[2] for x in shared} == {"customer:C2", "customer_category:C2:B"}
    assert len(overlaps) == len(set(overlaps))
    assert all(a != b for a, b, _ in overlaps)
    assert all(r["must_not_sum"] for r in rows if r["opportunity_id"] in ids)
    assert overlaps == build_opportunities(facts, policy(), AS_OF)[1]


def test_return_opportunity_requires_observed_category_peer_share() -> None:
    facts = opportunity_facts()
    facts["returns"] = [x for x in facts["returns"] if x["product_id"] == "P1"]
    row = next(
        r for r in build_opportunities(facts, policy(), AS_OF)[0] if r["opportunity_id"] == "O06:P1"
    )
    assert row["status"] == "investigative"
    assert all(row[f"estimated_impact_{bound}"] is None for bound in ("low", "base", "high"))
    assert json.loads(row["baseline"])["category_peer_defect_share"] is None
    assert json.loads(row["baseline"])["category_peer_accepted_events"] == 0

    facts["returns"].append(event("P2", date(2026, 8, 1), False))
    observed = next(
        r for r in build_opportunities(facts, policy(), AS_OF)[0] if r["opportunity_id"] == "O06:P1"
    )
    assert observed["status"] == "estimable"
    assert observed["estimated_impact_base"] == D("16.00")
    assert json.loads(observed["baseline"])["category_peer_defect_share"] == 0
    assert json.loads(observed["baseline"])["category_peer_accepted_events"] == 1


def insight_facts():
    facts = opportunity_facts()
    facts["monthly"] = []
    facts["customer_month"] = []
    facts["channel_month"] = []
    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            reporting = date(year, month, 1)
            if not (date(2024, 10, 1) <= reporting <= date(2026, 9, 1)):
                continue
            is_recent = reporting >= date(2025, 10, 1)
            rev, gp = (120, 18) if is_recent else (100, 30)
            for scope in ("LT", "LV", "EE", "COMPANY"):
                # LT falls; COMPANY grows. Every scope remains internally comparable.
                revenue = 80 if scope == "LT" and is_recent else rev
                facts["monthly"].append(
                    dict(
                        reporting_month=reporting,
                        scope=scope,
                        revenue=D(revenue),
                        gp=D(gp),
                        posting_count=10,
                    )
                )
            for cid in range(20):
                amount = (40 if cid < 5 else 1) if is_recent else 10
                facts["customer_month"].append(
                    dict(
                        reporting_month=reporting,
                        customer_id=f"X{cid}",
                        country="LT",
                        revenue=D(amount),
                    )
                )
            for channel, share in (
                (("A", 0.5), ("B", 0.5)) if not is_recent else (("A", 0.8), ("B", 0.2))
            ):
                facts["channel_month"].append(
                    dict(
                        reporting_month=reporting,
                        country="LT",
                        channel_code=channel,
                        revenue=D(str(rev * share)),
                    )
                )
    facts["returns"] = [event("P1", date(2025, 6, 1), False, idx=i) for i in range(10)] + [
        event("P1", date(2026, 6, 1), True, idx=i) for i in range(10)
    ]
    facts["cohorts"] = []
    for year, months, retained in ((2025, (10, 11, 12), 8), (2026, (1, 2, 3), 4)):
        for month in months:
            facts["cohorts"].append(
                dict(
                    cohort_month=date(year, month, 1),
                    country="LT",
                    cohort_age_month=6,
                    eligible_customer_count=10,
                    retained_customer_count=retained,
                    retention_rate=D(retained) / 10,
                    is_observation_mature=True,
                )
            )
    facts["forecast"] = [
        dict(
            scope="LV",
            origin_month=date(2026, 8, 1),
            target_month=date(2026, 9, 1),
            actual_eur=D(200),
            predicted_eur=D(100),
            error_eur=D(100),
            interval_status="calibrated",
            calibration_n=20,
            interval_n=15,
            interval95_low_eur=D(50),
            interval95_high_eur=D(150),
            coverage95=0.6,
        )
    ]
    return facts


def test_seven_insight_rules_golden_triggers_and_boundaries() -> None:
    facts = insight_facts()
    rows = evaluate_insights(facts, policy(), AS_OF)
    assert {r["rule_id"] for r in rows} == {f"R{i:02d}" for i in range(1, 8)}
    assert rows == evaluate_insights(facts, policy(), AS_OF)
    assert (
        next(r for r in rows if r["rule_id"] == "R01" and r["scope_code"] == "COMPANY")["status"]
        == "triggered"
    )
    assert next(r for r in rows if r["rule_id"] == "R02")["status"] == "triggered"
    assert (
        next(r for r in rows if r["rule_id"] == "R03" and r["scope_code"] == "LT")["status"]
        == "triggered"
    )
    assert next(r for r in rows if r["rule_id"] == "R04")["status"] == "triggered"
    assert next(r for r in rows if r["rule_id"] == "R05")["status"] == "triggered"
    assert (
        next(r for r in rows if r["rule_id"] == "R06" and r["scope_code"] == "LT")["status"]
        == "triggered"
    )
    forecast = next(r for r in rows if r["rule_id"] == "R07" and r["scope_code"] == "LV")
    assert (forecast["status"], forecast["severity"]) == ("triggered", "investigate")
    assert (
        forecast["management_interpretation"]
        == "Observed revenue lies beyond a calibrated historical error band."
    )
    facts["forecast"][0]["actual_eur"] = D(150)
    assert (
        next(
            r
            for r in evaluate_insights(facts, policy(), AS_OF)
            if r["rule_id"] == "R07" and r["scope_code"] == "LV"
        )["status"]
        == "not_triggered"
    )
    facts["forecast"][0]["interval_n"] = 1
    assert (
        next(
            r
            for r in evaluate_insights(facts, policy(), AS_OF)
            if r["rule_id"] == "R07" and r["scope_code"] == "LV"
        )["status"]
        == "insufficient_evidence"
    )


def test_near_threshold_rules_do_not_trigger() -> None:
    facts = insight_facts()
    limits = {
        "R01": ("margin_decline_points", 0.151, "COMPANY"),
        "R03": ("concentration_top5_threshold", 0.95, "LT"),
        "R04": ("retention_decline_threshold", 0.41, "COMPANY"),
        "R05": ("adjusted_discount_increase_points", 0.051, "COMPANY"),
        "R06": ("regional_revenue_decline", 0.21, "LT"),
    }
    for rule, (field, threshold, scope) in limits.items():
        adjusted = policy().model_copy(update={field: threshold})
        row = next(
            r
            for r in evaluate_insights(facts, adjusted, AS_OF)
            if r["rule_id"] == rule and r["scope_code"] == scope
        )
        assert row["status"] == "not_triggered", rule
    # The prior share rises, so the same recent defect share no longer clears
    # a 95-point deterioration threshold.
    facts["returns"][0]["reason_group"] = "product_quality"
    adjusted = policy().model_copy(update={"return_quality_increase_points": 0.95})
    assert (
        next(r for r in evaluate_insights(facts, adjusted, AS_OF) if r["rule_id"] == "R02")[
            "status"
        ]
        == "not_triggered"
    )


def test_missing_forecast_evidence_is_explicit() -> None:
    facts = insight_facts()
    facts["forecast"] = []
    rows = [r for r in evaluate_insights(facts, policy(), AS_OF) if r["rule_id"] == "R07"]
    assert [r["scope_code"] for r in rows] == ["COMPANY", "EE", "LT", "LV"]
    assert all(r["status"] == "insufficient_evidence" and r["numerator"] is None for r in rows)
