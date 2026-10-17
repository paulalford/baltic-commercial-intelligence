"""Deterministic, descriptive opportunity hypotheses and GP impact scenarios."""

# ruff: noqa: E501  # Long published formulas and limitations stay readable as strings.

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from statistics import median
from typing import Any

from baltic_sales_intelligence.opportunities.policy import OpportunityPolicy

CENT = Decimal("0.01")


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_EVEN)


def number(value: Decimal | float | int | None) -> float:
    return float(value or 0)


def scenario(basis: Decimal | float, rates: tuple[float, float, float], cap: Decimal | float):
    basis = max(Decimal(0), Decimal(str(basis)))
    cap = max(Decimal(0), Decimal(str(cap)))
    return tuple(money(min(basis * Decimal(str(rate)), cap)) for rate in rates)


def _row(
    code: str,
    entity_id: str,
    entity: str,
    as_of: date,
    *,
    window_start: date,
    status: str,
    problem: str,
    metric_ids: list[str],
    baseline: dict,
    impact: tuple | None,
    formula: str,
    assumptions: dict,
    limitation: str,
    next_step: str,
    owner: str,
    support: int,
    months: int,
    quality: float,
    overlap: list[str],
    policy: OpportunityPolicy,
) -> dict[str, Any]:
    if status != "estimable":
        impact = None
    return {
        "opportunity_id": f"{code}:{entity_id}",
        "opportunity_code": code,
        "as_of_date": as_of,
        "observation_start_date": window_start,
        "observation_end_date": as_of,
        "business_entity": entity,
        "entity_id": entity_id,
        "status": status,
        "problem_or_potential": problem,
        "evidence_metric_ids": metric_ids,
        "baseline": json.dumps(baseline, sort_keys=True, default=str),
        "estimated_impact_low": impact[0] if impact else None,
        "estimated_impact_base": impact[1] if impact else None,
        "estimated_impact_high": impact[2] if impact else None,
        "impact_unit": "EUR net gross profit, 12-month illustrative scenario" if impact else None,
        "estimation_formula": formula if impact else None,
        "assumptions": json.dumps(assumptions, sort_keys=True, default=str),
        "limitation": limitation,
        "recommended_next_step": next_step,
        "owner_role": owner,
        "method_version": policy.method_version,
        "support_count": support,
        "observation_months": months,
        "measurement_quality": quality,
        "overlap_ids": sorted(set(overlap)),
        "impact_score": 0.0,
        "confidence_score": 0.0,
        "actionability_score": 0.0,
        "urgency_score": 0.0,
        "score": 0.0,
        "must_not_sum": False,
    }


def _median_positive(values) -> Decimal:
    positive = sorted(Decimal(str(x)) for x in values if x is not None and x > 0)
    return money(median(positive)) if positive else Decimal(0)


def _cadence_days(account: dict) -> float:
    first, last = account["first_fulfilled_date"], account["last_fulfilled_date"]
    orders = account["lifetime_orders"]
    return (last - first).days / (orders - 1) if first and last and orders > 1 else 0.0


def build_opportunities(facts: dict[str, list[dict]], policy: OpportunityPolicy, as_of: date):
    """Return ranked rows and suppression counts; all estimates use gross profit."""
    customers = facts["customers"]
    customer_by_id = {c["customer_id"]: c for c in customers}
    subcats = facts["customer_subcategories"]
    by_customer: dict[str, list[dict]] = defaultdict(list)
    by_segment: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in customers:
        by_segment[(c["country"], c["segment"])].append(c)
    for x in subcats:
        by_customer[x["customer_id"]].append(x)
    rows: list[dict] = []
    suppressed: Counter[str] = Counter()
    rates = (policy.scenario_low, policy.scenario_base, policy.scenario_high)
    cap_rate = Decimal(str(policy.annual_impact_cap_fraction))
    windows = facts["windows"][0]

    # O01: the reference is observed annualised GP, limited by active same-segment peers.
    for c in customers:
        if (
            not c["is_lapsed_buyer"]
            or not c["first_fulfilled_date"]
            or not c["last_fulfilled_date"]
        ):
            continue
        peers = [
            p
            for p in by_segment[(c["country"], c["segment"])]
            if p["is_active_buyer"] and p["customer_id"] != c["customer_id"] and p["recent_gp"] > 0
        ]
        peer_gp = _median_positive(p["recent_gp"] for p in peers)
        own_cadence = _cadence_days(c)
        peer_cadences = [_cadence_days(p) for p in peers if _cadence_days(p) > 0]
        peer_cadence = float(median(peer_cadences)) if peer_cadences else 0.0
        tenure_years = max(number(c["tenure_days"]) / 365.25, 1.0)
        annual_gp = money(number(c["lifetime_gp"]) / tenure_years)
        basis = min(max(annual_gp, Decimal(0)), peer_gp)
        status = (
            "estimable"
            if c["lifetime_orders"] >= policy.minimum_customer_orders
            and len(peers) >= policy.minimum_peer_accounts
            and basis > 0
            and number(c["recency_days"])
            >= (policy.lapse_cadence_multiplier * max(own_cadence, peer_cadence))
            else "investigative"
        )
        rows.append(
            _row(
                "O01",
                c["customer_id"],
                c["customer_name"],
                as_of,
                window_start=c["first_fulfilled_date"],
                status=status,
                problem="Lapsed account with historically positive contribution",
                metric_ids=[
                    "mart_rfm.is_lapsed_buyer",
                    "mart_customer_value.historical_value_net_gross_profit_eur",
                ],
                baseline={
                    "lifetime_gp_eur": c["lifetime_gp"],
                    "annualised_gp_eur": annual_gp,
                    "peer_median_recent_gp_eur": peer_gp,
                    "own_purchase_cadence_days": own_cadence,
                    "peer_median_purchase_cadence_days": peer_cadence,
                    "recency_days": c["recency_days"],
                    "last_fulfilled_date": c["last_fulfilled_date"],
                },
                impact=scenario(basis, rates, min(basis, max(annual_gp, Decimal(0)) * cap_rate)),
                formula=(
                    "min(annualised historical GP, active peer median GP) * "
                    f"scenario response; cap at {policy.annual_impact_cap_fraction:.0%} "
                    "of annualised historical GP"
                ),
                assumptions={
                    "response_scenarios": rates,
                    "peer_group": "same current country and segment",
                    "cap_fraction": policy.annual_impact_cap_fraction,
                    "cadence_multiplier": policy.lapse_cadence_multiplier,
                },
                limitation="Historical cadence and peer contribution do not prove reactivation or future demand.",
                next_step="Review account history and contact suitability before outreach.",
                owner="Account manager",
                support=len(peers),
                months=max(1, (as_of - c["first_fulfilled_date"]).days // 30),
                quality=policy.measurement_quality["O01"],
                overlap=[f"customer:{c['customer_id']}"],
                policy=policy,
            )
        )

    # O02: supported basket association plus local segment context and actual peer GP.
    purchase = {(x["customer_id"], x["subcategory_id"]) for x in subcats}
    gp_by_peer_cohort: dict[tuple[str, str, str], list[Decimal]] = defaultdict(list)
    for x in subcats:
        buyer = customer_by_id.get(x["customer_id"])
        if buyer and buyer["is_active_buyer"] and x["gp_eur"] > 0:
            gp_by_peer_cohort[(buyer["country"], buyer["segment"], x["subcategory_id"])].append(
                x["gp_eur"]
            )
    context = {}
    for x in facts["basket_context"]:
        for a, b, confidence, metric_id in (
            (
                x["subcategory_a_id"],
                x["subcategory_b_id"],
                x["confidence_a_to_b"],
                "mart_basket_context.confidence_a_to_b",
            ),
            (
                x["subcategory_b_id"],
                x["subcategory_a_id"],
                x["confidence_b_to_a"],
                "mart_basket_context.confidence_b_to_a",
            ),
        ):
            context[(x["country"], x["segment"], a, b)] = (x, confidence, metric_id)
    for pair in facts["basket_pairs"]:
        if (
            pair["joint_baskets"] < policy.minimum_pair_joint_baskets
            or number(pair.get("pair_support")) < policy.minimum_pair_support
            or number(pair["lift"]) < policy.minimum_pair_lift
            or pair.get("q_value") is None
            or number(pair["q_value"]) > policy.maximum_pair_q_value
            or pair["status"] not in {"association_only", "supported"}
        ):
            suppressed["O02:unsupported_pair"] += 1
            continue
        for a, b in (
            (pair["subcategory_a_id"], pair["subcategory_b_id"]),
            (pair["subcategory_b_id"], pair["subcategory_a_id"]),
        ):
            for c in customers:
                cid = c["customer_id"]
                if not c["is_active_buyer"] or (cid, a) not in purchase or (cid, b) in purchase:
                    continue
                peer_contributions = gp_by_peer_cohort[(c["country"], c["segment"], b)]
                peer_gp = _median_positive(peer_contributions)
                context_entry = context.get((c["country"], c["segment"], a, b))
                ctx, observed_attach, attach_metric = (
                    context_entry if context_entry else (None, None, None)
                )
                if attach_metric is None:
                    attach_metric = (
                        "mart_basket_context.confidence_a_to_b"
                        if a == pair["subcategory_a_id"]
                        else "mart_basket_context.confidence_b_to_a"
                    )
                local_n = ctx["joint_baskets"] if ctx else 0
                supported = (
                    ctx is not None
                    and ctx["context_status"] == "threshold_met"
                    and local_n >= policy.minimum_context_baskets
                    and observed_attach is not None
                    and 0 < number(observed_attach) <= 1
                    and len(peer_contributions) >= policy.minimum_peer_accounts
                    and peer_gp > 0
                )
                own_gp = max(Decimal(0), c["recent_gp"])
                cap = own_gp * cap_rate
                attach_factors = (
                    policy.cross_sell_attach_low,
                    policy.cross_sell_attach_base,
                    policy.cross_sell_attach_high,
                )
                rows.append(
                    _row(
                        "O02",
                        f"{cid}:{a}:{b}",
                        c["customer_name"],
                        as_of,
                        window_start=windows["recent_start"],
                        status="estimable" if supported and cap > 0 else "investigative",
                        problem=f"Observed {a} purchase without associated {b}",
                        metric_ids=[
                            "basket_pair_result.lift",
                            "mart_basket_context.joint_baskets",
                            attach_metric,
                            "mart_commercial_month.net_gross_profit_eur",
                        ],
                        baseline={
                            "pair_joint_baskets": pair["joint_baskets"],
                            "pair_lift": pair["lift"],
                            "local_joint_baskets": local_n,
                            "observed_local_a_to_b_attachment": observed_attach,
                            "comparable_positive_b_peer_accounts": len(peer_contributions),
                            "peer_subcategory_median_gp_eur": peer_gp,
                            "account_recent_gp_eur": c["recent_gp"],
                        },
                        impact=scenario(
                            peer_gp * Decimal(str(observed_attach or 0)), attach_factors, cap
                        ),
                        formula=(
                            "median positive trailing-year B-subcategory net GP among active "
                            "same-country, same-segment B-buying peers * local directional "
                            "A-to-B joint/A basket attachment * conservative scenario factor; "
                            "cap at account trailing-year net GP fraction"
                        ),
                        assumptions={
                            "attachment_scenario_factors": attach_factors,
                            "peer_cohort": "active same-current-country and same-current-segment B buyers with positive trailing-year B-subcategory net GP",
                            "peer_support_denominator": "distinct eligible B-buying peer accounts",
                            "attachment_denominator": "local fulfilled order baskets containing A",
                            "cap_fraction": policy.annual_impact_cap_fraction,
                        },
                        limitation="Basket attachment is observational and differs from account adoption; eligible account may have no need or capacity.",
                        next_step="Validate account need and product suitability.",
                        owner="Account manager",
                        support=local_n,
                        months=policy.observation_months,
                        quality=policy.measurement_quality["O02"],
                        overlap=[f"customer:{cid}", f"customer_category:{cid}:{b}"],
                        policy=policy,
                    )
                )

    # O03: same-SKU, comparable-year sale economics. Recoverable unit GP is capped.
    for p in facts["products"]:
        ru, pu = p["recent_units"], p["prior_units"]
        if ru < policy.minimum_product_units or pu < policy.minimum_product_units:
            suppressed["O03:insufficient_same_sku_units"] += 1
            continue
        recent_rate = number(p["recent_discount"]) / max(number(p["recent_list"]), 1e-12)
        prior_rate = number(p["prior_discount"]) / max(number(p["prior_list"]), 1e-12)
        discount_gap = recent_rate - prior_rate
        gp_gap = number(p["prior_gp"]) / pu - number(p["recent_gp"]) / ru
        if discount_gap < policy.margin_discount_gap and gp_gap <= 0:
            continue
        list_per_unit = number(p["recent_list"]) / ru
        discount_recovery = max(0, discount_gap) * list_per_unit
        unit_recovery = min(
            max(discount_recovery, gp_gap), list_per_unit * policy.margin_unit_recovery_cap_fraction
        )
        basis = money(ru * unit_recovery)
        cap = money(
            min(
                number(p["recent_list"]) * policy.margin_unit_recovery_cap_fraction,
                number(p["prior_gp"]) if p["prior_gp"] > 0 else 0,
            )
        )
        rows.append(
            _row(
                "O03",
                p["product_id"],
                f"Product {p['product_id']}",
                as_of,
                window_start=windows["prior_start"],
                status="estimable" if basis > 0 and cap > 0 else "investigative",
                problem="Same-SKU discount increase or unit gross-profit squeeze",
                metric_ids=[
                    "fact_order_line.posted_discount_eur",
                    "fact_order_line.posted_gross_profit_eur",
                ],
                baseline={
                    "recent_units": ru,
                    "prior_units": pu,
                    "recent_discount_rate": recent_rate,
                    "prior_discount_rate": prior_rate,
                    "unit_gp_gap_eur": gp_gap,
                    "recent_sale_gp_eur": p["recent_gp"],
                },
                impact=scenario(basis, rates, cap),
                formula=(
                    "recent eligible units * min(max(discount gap * list/unit, "
                    "same-SKU GP/unit gap), "
                    f"{policy.margin_unit_recovery_cap_fraction:.0%} list/unit) * uptake; "
                    "cap at prior same-SKU GP and configured fraction of recent list"
                ),
                assumptions={
                    "uptake_scenarios": rates,
                    "same_sku_comparison": True,
                    "unit_recovery_cap_fraction": policy.margin_unit_recovery_cap_fraction,
                },
                limitation="Cost and discount changes are not independently causal; price elasticity is unknown.",
                next_step="Check contract terms, cost snapshots and comparable quotes.",
                owner="Commercial manager",
                support=min(ru, pu),
                months=24,
                quality=policy.measurement_quality["O03"],
                overlap=[f"product:{p['product_id']}", f"category:{p['category_id']}"],
                policy=policy,
            )
        )

    # O04: category adoption among peers in the same current segment and country.
    categories = sorted({x["category_id"] for x in subcats})
    category_members: dict[tuple[str, str, str], dict[str, Decimal]] = defaultdict(dict)
    for x in subcats:
        c = customer_by_id.get(x["customer_id"])
        if c and c["is_active_buyer"]:
            members = category_members[(c["country"], c["segment"], x["category_id"])]
            members[c["customer_id"]] = members.get(c["customer_id"], Decimal(0)) + x["gp_eur"]
    for c in customers:
        if not c["is_active_buyer"] or c["recent_gp"] <= 0:
            continue
        owned = {x["category_id"] for x in by_customer[c["customer_id"]]}
        for category in categories:
            if category in owned:
                continue
            peers = category_members[(c["country"], c["segment"], category)]
            segment_n = sum(p["is_active_buyer"] for p in by_segment[(c["country"], c["segment"])])
            peer_gp = _median_positive(peers.values())
            supported = len(peers) >= policy.minimum_peer_accounts and peer_gp > 0
            uptake = min(len(peers) / segment_n, 1.0) if segment_n else 0.0
            basis = peer_gp * Decimal(str(uptake))
            cap = max(Decimal(0), c["recent_gp"]) * cap_rate
            rows.append(
                _row(
                    "O04",
                    f"{c['customer_id']}:{category}",
                    c["customer_name"],
                    as_of,
                    window_start=windows["recent_start"],
                    status="estimable" if supported and cap > 0 else "investigative",
                    problem=f"Missing category {category} purchased by segment peers",
                    metric_ids=[
                        "mart_commercial_month.fulfilled_sales_eur",
                        "mart_commercial_month.net_gross_profit_eur",
                    ],
                    baseline={
                        "peer_accounts": len(peers),
                        "segment_accounts": segment_n,
                        "peer_penetration": uptake,
                        "peer_median_category_gp_eur": peer_gp,
                    },
                    impact=scenario(basis, rates, cap),
                    formula="same-segment peer median category GP * observed peer penetration * uptake scenario; cap at account GP fraction",
                    assumptions={
                        "uptake_scenarios": rates,
                        "cap_fraction": policy.annual_impact_cap_fraction,
                    },
                    limitation="Peer penetration is descriptive and may reflect different account needs.",
                    next_step="Confirm category relevance with the account owner.",
                    owner="Account manager",
                    support=len(peers),
                    months=policy.observation_months,
                    quality=policy.measurement_quality["O04"],
                    overlap=[
                        f"customer:{c['customer_id']}",
                        f"customer_category:{c['customer_id']}:{category}",
                    ],
                    policy=policy,
                )
            )

    # O05: recent positive GP and revenue movement, bounded by peer GP changes.
    for c in customers:
        prior, recent = c["prior_gp"], c["recent_gp"]
        if not c["is_active_buyer"] or prior <= 0 or recent <= prior:
            continue
        rate = number(recent / prior - 1)
        if rate < policy.growth_min_rate or c["recent_revenue"] <= c["prior_revenue"]:
            continue
        peers = [
            p
            for p in by_segment[(c["country"], c["segment"])]
            if p["customer_id"] != c["customer_id"]
            and p["prior_gp"] > 0
            and p["recent_gp"] > p["prior_gp"]
        ]
        peer_delta = _median_positive(p["recent_gp"] - p["prior_gp"] for p in peers)
        basis = min(recent - prior, peer_delta)
        cap = recent * cap_rate
        rows.append(
            _row(
                "O05",
                c["customer_id"],
                c["customer_name"],
                as_of,
                window_start=windows["prior_start"],
                status="estimable"
                if len(peers) >= policy.minimum_peer_accounts and basis > 0
                else "investigative",
                problem="Positive comparable-year gross-profit and revenue trajectory",
                metric_ids=[
                    "mart_commercial_month.net_gross_profit_eur",
                    "mart_commercial_month.net_revenue_eur",
                ],
                baseline={
                    "recent_gp_eur": recent,
                    "prior_gp_eur": prior,
                    "gp_growth_rate": rate,
                    "peer_median_gp_delta_eur": peer_delta,
                },
                impact=scenario(basis, rates, cap),
                formula="min(account comparable-year GP gain, peer median GP gain) * scenario; cap at recent account GP fraction",
                assumptions={"growth_scenarios": rates, "peer_group": "same country and segment"},
                limitation="Observed growth is not a forecast and may reflect one-off orders or mix.",
                next_step="Check repeatability and service capacity before planning expansion.",
                owner="Account manager",
                support=len(peers),
                months=24,
                quality=policy.measurement_quality["O05"],
                overlap=[f"customer:{c['customer_id']}"],
                policy=policy,
            )
        )

    # O06: accepted defect returns on decision-date basis; no mature return-rate claim.
    by_product: dict[str, list[dict]] = defaultdict(list)
    by_category: dict[str, list[dict]] = defaultdict(list)
    product_category = {p["product_id"]: p["category_id"] for p in facts["products"]}
    for event in facts["returns"]:
        if event["return_decision_date"] > as_of:
            continue
        by_product[event["product_id"]].append(event)
        by_category[event["category_id"]].append(event)
    for product_id, events in by_product.items():
        recent_events = [x for x in events if x["return_decision_date"] >= windows["recent_start"]]
        defect = [x for x in recent_events if x["reason_group"] == "product_quality"]
        if len(defect) < policy.minimum_defect_events:
            suppressed["O06:insufficient_defect_events"] += 1
            continue
        shares = []
        for start, end in (
            (windows["previous_quarter"], windows["quarter_start"]),
            (windows["quarter_start"], as_of),
        ):
            q = [
                x
                for x in recent_events
                if start <= x["return_decision_date"]
                and (
                    x["return_decision_date"] < end
                    if end != as_of
                    else x["return_decision_date"] <= end
                )
            ]
            count = sum(x["reason_group"] == "product_quality" for x in q)
            shares.append((count, len(q), count / len(q) if q else 0))
        category_events = [
            x
            for x in by_category[product_category[product_id]]
            if x["product_id"] != product_id
            and x["return_decision_date"] >= windows["recent_start"]
        ]
        category_share = (
            sum(x["reason_group"] == "product_quality" for x in category_events)
            / len(category_events)
            if category_events
            else None
        )
        sustained = all(
            n >= policy.minimum_defect_quarter_events
            and share >= policy.defect_share_threshold
            and category_share is not None
            and share >= category_share + policy.defect_peer_excess
            for n, total, share in shares
        )
        gp_loss = sum(
            (
                max(Decimal(0), x["posted_refund_ex_vat_eur"] - x["posted_recovered_cogs_eur"])
                for x in defect
            ),
            Decimal(0),
        )
        rows.append(
            _row(
                "O06",
                product_id,
                f"Product {product_id}",
                as_of,
                window_start=windows["recent_start"],
                status="estimable" if sustained and gp_loss > 0 else "investigative",
                problem="Sustained defect-related accepted returns",
                metric_ids=[
                    "mart_product_return_event.reason_group",
                    "mart_product_return_event.posted_refund_ex_vat_eur",
                    "mart_product_return_event.posted_recovered_cogs_eur",
                ],
                baseline={
                    "defect_events_12m": len(defect),
                    "two_quarter_defect_shares": shares,
                    "category_peer_accepted_events": len(category_events),
                    "category_peer_defect_share": category_share,
                    "defect_gp_reversal_eur": gp_loss,
                },
                impact=scenario(gp_loss, rates, gp_loss),
                formula="observed defect accepted-return GP reversal * plausible improvement scenario; cap at observed reversal",
                assumptions={
                    "improvement_scenarios": rates,
                    "basis": "decision-period accepted events",
                },
                limitation="Accepted-return share is not a mature fulfilled-unit return rate; prevention and COGS recovery are uncertain.",
                next_step="Review defect causes and supplier quality records.",
                owner="Product quality manager",
                support=len(defect),
                months=policy.observation_months,
                quality=policy.measurement_quality["O06"],
                overlap=[f"product:{product_id}", f"category:{product_category[product_id]}"],
                policy=policy,
            )
        )

    # Rank deterministically. Rows beyond the per-family management limit are counted.
    max_impact = max((number(r["estimated_impact_base"]) for r in rows), default=0)
    w = policy.score_weights
    support_minimum = {
        "O01": policy.minimum_peer_accounts,
        "O02": policy.minimum_context_baskets,
        "O03": policy.minimum_product_units,
        "O04": policy.minimum_peer_accounts,
        "O05": policy.minimum_peer_accounts,
        "O06": policy.minimum_defect_events,
    }
    for r in rows:
        r["impact_score"] = (
            round(100 * number(r["estimated_impact_base"]) / max_impact, 2) if max_impact else 0.0
        )
        evidence = min(r["support_count"] / support_minimum[r["opportunity_code"]], 1.0)
        history = min(r["observation_months"] / 24, 1.0)
        sensitivity = (
            number(r["estimated_impact_low"]) / number(r["estimated_impact_high"])
            if r["estimated_impact_high"]
            else 0.0
        )
        r["confidence_score"] = round(
            100
            * (
                policy.confidence_evidence_weight * evidence
                + policy.confidence_history_weight * history
                + policy.confidence_quality_weight * r["measurement_quality"]
                + policy.confidence_sensitivity_weight * sensitivity
            ),
            2,
        )
        r["actionability_score"] = (
            policy.actionability_score_estimable
            if r["status"] == "estimable"
            else policy.actionability_score_investigative
        )
        r["urgency_score"] = policy.urgency_scores[r["opportunity_code"]]
        r["score"] = round(
            w.impact * r["impact_score"]
            + w.confidence * r["confidence_score"]
            + w.actionability * r["actionability_score"]
            + w.urgency * r["urgency_score"],
            2,
        )
    ranked = []
    for code in ("O01", "O02", "O03", "O04", "O05", "O06"):
        group = [r for r in rows if r["opportunity_code"] == code]
        group.sort(key=lambda r: (-r["score"], r["opportunity_code"], r["entity_id"]))
        ranked.extend(group[: policy.candidate_limit_per_family])
        suppressed[f"{code}:management_limit"] += max(
            0, len(group) - policy.candidate_limit_per_family
        )
    ranked.sort(key=lambda r: (-r["score"], r["opportunity_code"], r["entity_id"]))
    overlap_index: dict[str, list[dict]] = defaultdict(list)
    for row in ranked:
        if row["status"] == "estimable":
            for identifier in row["overlap_ids"]:
                overlap_index[identifier].append(row)
    relationships = set()
    for identifier, members in overlap_index.items():
        if len(members) < 2:
            continue
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                if left["opportunity_id"] == right["opportunity_id"]:
                    continue
                left["must_not_sum"] = right["must_not_sum"] = True
                a, b = sorted((left["opportunity_id"], right["opportunity_id"]))
                relationships.add((a, b, identifier))
    return ranked, sorted(relationships), dict(sorted(suppressed.items()))
