"""Pure, fixture-testable estimators for the five governed statistical studies."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

import numpy as np
import pandas as pd
import patsy
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.special import expit
from scipy.stats import norm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.tools.sm_exceptions import PerfectSeparationError

SEED = 12026
CENT = Decimal("0.01")


def bh_adjust(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg q values, preserving caller order."""
    if not p_values:
        return []
    n = len(p_values)
    order = np.argsort(p_values, kind="stable")
    result = np.empty(n, dtype=float)
    running = 1.0
    for rank in range(n - 1, -1, -1):
        index = int(order[rank])
        running = min(running, float(p_values[index]) * n / (rank + 1))
        result[index] = running
    return result.tolist()


def wilson_interval(
    successes: int, trials: int, z: float = 1.959963984540054
) -> tuple[float, float] | None:
    """Wilson interval for a fixture with defensible independent Bernoulli trials."""
    if trials <= 0 or not 0 <= successes <= trials:
        return None
    p = successes / trials
    d = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / d
    half = z * np.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / d
    return max(0.0, float(centre - half)), min(1.0, float(centre + half))


@dataclass(frozen=True)
class CohortSale:
    line_id: str
    fulfilled_date: date
    units: int
    category_id: str = "fixture"


@dataclass(frozen=True)
class CohortReturn:
    return_id: str
    line_id: str
    decision_date: date
    accepted_units: int
    accepted: bool


def mature_cohort_rate(
    sales: list[CohortSale],
    returns: list[CohortReturn],
    *,
    as_of: date,
    maturity_days: int,
    category_id: str | None = None,
) -> dict[str, Any]:
    """Fixture-only cohort arithmetic; the production maturity duration is unapproved."""
    if maturity_days < 0:
        raise ValueError("maturity_days must be explicit and nonnegative")
    if len({x.line_id for x in sales}) != len(sales):
        raise ValueError("duplicate sale line")
    if len({x.return_id for x in returns}) != len(returns):
        raise ValueError("duplicate return event")
    if any(x.units <= 0 for x in sales) or any(x.accepted_units < 0 for x in returns):
        raise ValueError("invalid cohort unit count")
    sale_ids = {x.line_id for x in sales}
    if any(x.line_id not in sale_ids for x in returns):
        raise ValueError("orphan return event")
    eligible = {
        x.line_id: x
        for x in sales
        if x.fulfilled_date <= as_of - timedelta(days=maturity_days)
        and (category_id is None or x.category_id == category_id)
    }
    accepted = defaultdict(int)
    for event in returns:
        if event.accepted and event.decision_date <= as_of and event.line_id in eligible:
            if event.decision_date < eligible[event.line_id].fulfilled_date:
                raise ValueError("return precedes sale")
            accepted[event.line_id] += event.accepted_units
    if any(units > eligible[line].units for line, units in accepted.items()):
        raise ValueError("accepted return cap exceeded")
    denominator = sum(x.units for x in eligible.values())
    numerator = sum(accepted.values())
    return {
        "mature_units": denominator,
        "accepted_units": numerator,
        "rate": numerator / denominator if denominator else None,
        "eligible_lines": len(eligible),
    }


def _cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def price_mix_cost_bridge(
    sku_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exact posted-cent EE GP bridge; price/mix/cost are descriptive components."""
    by_sku: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in sku_rows:
        period = row["period"]
        if period not in ("prior", "current") or (
            row["product_id"] in by_sku and period in by_sku[row["product_id"]]
        ):
            raise ValueError("duplicate or invalid SKU period")
        by_sku[row["product_id"]][period] = row
    market = {row["period"]: row for row in market_rows}
    if set(market) != {"prior", "current"}:
        raise ValueError("both market periods are required")

    def dec(row: dict[str, Any], field: str) -> Decimal:
        return Decimal(str(row[field]))

    with localcontext() as context:
        context.prec = 38
        prior_fulfilled = sum(
            (dec(x, "fulfilled_sales_eur") for x in sku_rows if x["period"] == "prior"), Decimal(0)
        )
        current_fulfilled = sum(
            (dec(x, "fulfilled_sales_eur") for x in sku_rows if x["period"] == "current"),
            Decimal(0),
        )
        if prior_fulfilled != dec(
            market["prior"], "fulfilled_sales_eur"
        ) or current_fulfilled != dec(market["current"], "fulfilled_sales_eur"):
            raise ValueError("EE fulfilled sale bridge does not match Gold")
        matched = [r for r in by_sku.values() if "prior" in r and "current" in r]
        q0 = sum(int(r["prior"]["units"]) for r in matched)
        q1 = sum(int(r["current"]["units"]) for r in matched)
        pure_volume = mix = price = cost_volume_mix = unit_cost = Decimal(0)
        for r in matched:
            a, b = r["prior"], r["current"]
            n0, n1 = int(a["units"]), int(b["units"])
            if n0 <= 0 or n1 <= 0:
                raise ValueError("matched SKU units must be positive")
            p0 = dec(a, "fulfilled_sales_eur") / n0
            p1 = dec(b, "fulfilled_sales_eur") / n1
            c0 = dec(a, "fulfilled_cogs_eur") / n0
            c1 = dec(b, "fulfilled_cogs_eur") / n1
            pure_volume += Decimal(q1 - q0) * Decimal(n0) / Decimal(q0) * p0
            mix += (Decimal(n1) - Decimal(q1) * Decimal(n0) / Decimal(q0)) * p0
            price += Decimal(n1) * (p1 - p0)
            cost_volume_mix -= Decimal(n1 - n0) * c0
            unit_cost -= Decimal(n1) * (c1 - c0)
        new_exit = Decimal(0)
        for r in by_sku.values():
            if "prior" not in r:
                x = r["current"]
                new_exit += dec(x, "fulfilled_sales_eur") - dec(x, "fulfilled_cogs_eur")
            if "current" not in r:
                x = r["prior"]
                new_exit -= dec(x, "fulfilled_sales_eur") - dec(x, "fulfilled_cogs_eur")

        def fulfilled_gp(period: str) -> Decimal:
            return sum(
                (
                    dec(x, "fulfilled_sales_eur") - dec(x, "fulfilled_cogs_eur")
                    for x in sku_rows
                    if x["period"] == period
                ),
                Decimal(0),
            )

        return_timing = (
            dec(market["current"], "net_gross_profit_eur")
            - fulfilled_gp("current")
            - dec(market["prior"], "net_gross_profit_eur")
            + fulfilled_gp("prior")
        )
        observed = dec(market["current"], "net_gross_profit_eur") - dec(
            market["prior"], "net_gross_profit_eur"
        )
        components = {
            "pure_volume_price": _cent(pure_volume),
            "matched_mix_price": _cent(mix),
            "matched_price": _cent(price),
            "matched_cost_volume_mix": _cent(cost_volume_mix),
            "matched_unit_cost": _cent(unit_cost),
            "new_exited_fulfilled_gp": _cent(new_exit),
            "return_timing_gp": _cent(return_timing),
        }
        components["rounding_or_unallocated_residual"] = observed - sum(
            components.values(), Decimal(0)
        )
        if sum(components.values(), Decimal(0)) != observed:
            raise ValueError("EE GP bridge did not reconcile")
        return {
            "components": components,
            "observed_change_eur": observed,
            "prior_net_gp_eur": dec(market["prior"], "net_gross_profit_eur"),
            "current_net_gp_eur": dec(market["current"], "net_gross_profit_eur"),
            "matched_sku_count": len(matched),
            "new_or_exited_sku_count": len(by_sku) - len(matched),
        }


def market_margin_contrast(
    monthly: pd.DataFrame, *, seed: int = SEED, replicates: int = 1000
) -> dict[str, Any]:
    """EE minus pooled LT/LV 12-month margin change; paired 3-month block bootstrap."""
    if len(monthly["reporting_month"].unique()) != 24:
        return {"status": "not_estimable", "reason": "requires_24_complete_months"}
    frame = monthly.copy()
    frame["reporting_month"] = pd.to_datetime(frame["reporting_month"])
    months = sorted(frame["reporting_month"].unique())
    countries = ("EE", "LT", "LV")
    values = np.zeros((2, 12, 3, 2), dtype=float)
    exact: dict[tuple[int, str], tuple[Decimal, Decimal]] = {}
    for y in range(2):
        for ci, country in enumerate(countries):
            subset = frame[
                (frame["home_country_code"] == country)
                & frame["reporting_month"].isin(months[y * 12 : (y + 1) * 12])
            ]
            if len(subset) != 12:
                return {"status": "not_estimable", "reason": "missing_market_month"}
            subset = subset.set_index("reporting_month").loc[months[y * 12 : (y + 1) * 12]]
            revenue = sum((Decimal(str(v)) for v in subset["net_revenue_eur"]), Decimal(0))
            gp = sum((Decimal(str(v)) for v in subset["net_gross_profit_eur"]), Decimal(0))
            exact[y, country] = revenue, gp
            values[y, :, ci, 0] = subset["net_revenue_eur"].astype(float).to_numpy()
            values[y, :, ci, 1] = subset["net_gross_profit_eur"].astype(float).to_numpy()

    def contrast(source: dict[tuple[int, str], tuple[Decimal, Decimal]]) -> float | None:
        def margin(year: int, markets: tuple[str, ...]) -> Decimal | None:
            rev = sum((source[year, x][0] for x in markets), Decimal(0))
            gp = sum((source[year, x][1] for x in markets), Decimal(0))
            return gp / rev if rev > 0 else None

        before_ee, after_ee = margin(0, ("EE",)), margin(1, ("EE",))
        before_peer, after_peer = margin(0, ("LT", "LV")), margin(1, ("LT", "LV"))
        if None in (before_ee, after_ee, before_peer, after_peer):
            return None
        return float(((after_ee - before_ee) - (after_peer - before_peer)) * 100)

    point = contrast(exact)
    if point is None:
        return {"status": "not_estimable", "reason": "nonpositive_revenue"}
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(replicates):
        starts = rng.integers(0, 12, size=4)
        indices = np.concatenate([(start + np.arange(3)) % 12 for start in starts])
        draw = values[:, indices].sum(axis=1)
        if np.any(draw[:, :, 0] <= 0):
            continue
        margins = draw[:, :, 1] / draw[:, :, 0]
        peers = draw[:, 1:, 1].sum(axis=1) / draw[:, 1:, 0].sum(axis=1)
        samples.append(float(((margins[1, 0] - margins[0, 0]) - (peers[1] - peers[0])) * 100))
    if len(samples) < max(100, replicates * 0.9):
        return {"status": "insufficient_evidence", "reason": "unstable_bootstrap_denominator"}
    low, high = np.quantile(samples, [0.025, 0.975])
    standard_error = float(np.std(samples, ddof=1))
    p_value = float(2 * norm.sf(abs(point / standard_error))) if standard_error > 0 else None
    return {
        "status": "association_only",
        "estimate": point,
        "ci_low": float(low),
        "ci_high": float(high),
        "p_value": p_value,
        "standard_error": standard_error,
        "replicates": len(samples),
        "seed": seed,
        "block_months": 3,
        "n_month_pairs": 12,
        "n": 72,
    }


def fit_discount(sample: pd.DataFrame) -> dict[str, Any]:
    """Revenue-weighted realised margin association with customer-clustered SE."""
    required = [
        "customer_id",
        "reporting_month",
        "category_id",
        "segment_code",
        "home_country_code",
        "prior_account_discount_rate",
        "realised_margin_rate",
        "tenure_months",
        "net_revenue_eur",
    ]
    missing = sample[required].isna().any(axis=1)
    nonmissing = sample.loc[~missing]
    eligible = nonmissing.loc[nonmissing["net_revenue_eur"] > 0].copy()
    eligible["reporting_month"] = eligible["reporting_month"].astype(str)
    n_clusters = eligible["customer_id"].nunique()
    if (
        n_clusters < 30
        or len(eligible) < 100
        or eligible["prior_account_discount_rate"].std() < 1e-6
    ):
        return {
            "status": "insufficient_evidence",
            "reason": "small_or_invariant_sample",
            "n": len(eligible),
            "clusters": n_clusters,
            "missing": int(missing.sum()),
        }
    formula = (
        "realised_margin_rate ~ prior_account_discount_rate + np.log1p(tenure_months) "
        "+ C(category_id) + C(segment_code) + C(home_country_code) + C(reporting_month)"
    )
    weights = eligible["net_revenue_eur"].astype(float)
    try:
        model = smf.wls(formula, data=eligible, weights=weights)
        rank = np.linalg.matrix_rank(model.exog)
        if rank != model.exog.shape[1] or len(eligible) <= rank + 10:
            return {
                "status": "not_estimable",
                "reason": "collinear_or_overparameterized",
                "n": len(eligible),
                "clusters": n_clusters,
                "rank": int(rank),
            }
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": eligible["customer_id"]})
        term = "prior_account_discount_rate"
        beta, se = float(fit.params[term]), float(fit.bse[term])
        if not np.isfinite(beta) or not np.isfinite(se) or se <= 0:
            raise ValueError("unstable coefficient")
        largest = eligible.groupby("customer_id")["net_revenue_eur"].sum().astype(float).idxmax()
        sensitivity = smf.wls(
            formula,
            data=eligible[eligible.customer_id != largest],
            weights=weights[eligible.customer_id != largest],
        ).fit()
        bp_p = float(het_breuschpagan(fit.resid, model.exog)[1])
        discount_q = np.quantile(eligible[term], [0.25, 0.5, 0.75])
        margin_q = np.quantile(eligible["realised_margin_rate"], [0.25, 0.5, 0.75])
        scale = 10.0  # coefficient x 0.10 discount contrast x 100 percentage points
        return {
            "status": "association_only",
            "estimate": beta * scale,
            "ci_low": (beta - 1.959963984540054 * se) * scale,
            "ci_high": (beta + 1.959963984540054 * se) * scale,
            "p_value": float(fit.pvalues[term]),
            "n": len(eligible),
            "clusters": n_clusters,
            "missing": int(missing.sum()),
            "rank": int(rank),
            "condition_number": float(fit.condition_number),
            "heteroskedasticity_bp_p": bp_p,
            "prior_discount_median": float(discount_q[1]),
            "prior_discount_iqr": float(discount_q[2] - discount_q[0]),
            "realised_margin_median": float(margin_q[1]),
            "realised_margin_iqr": float(margin_q[2] - margin_q[0]),
            "realised_margin_mad": float(
                np.median(np.abs(eligible["realised_margin_rate"] - margin_q[1]))
            ),
            "max_standardized_residual": float(np.max(np.abs(fit.resid)) / np.std(fit.resid)),
            "largest_customer_revenue_share": float(
                eligible.groupby("customer_id")["net_revenue_eur"].sum().astype(float).max()
                / weights.sum()
            ),
            "drop_largest_customer_estimate": float(sensitivity.params[term]) * scale,
            "r_squared": float(fit.rsquared),
        }
    except (ValueError, np.linalg.LinAlgError, KeyError) as exc:
        return {
            "status": "not_estimable",
            "reason": f"regression_failure:{type(exc).__name__}",
            "n": len(eligible),
            "clusters": n_clusters,
            "missing": int(missing.sum()),
        }


def fit_retention(sample: pd.DataFrame, *, seed: int = SEED, draws: int = 400) -> dict[str, Any]:
    """Cluster-robust logistic model and pooled-covariate segment standardisation."""
    required = {
        "customer_id",
        "landmark_month",
        "outcome_mature",
        "retained_next_12m",
        "prior_12m_orders",
        "segment_code",
        "home_country_code",
        "tenure_months",
    }
    absent = sorted(required.difference(sample.columns))
    if absent:
        return {
            "status": "not_estimable",
            "reason": "invalid_input:missing_required_columns",
            "failure_category": "invalid_input",
            "missing_columns": {name: True for name in absent},
            "n": len(sample),
            "clusters": sample["customer_id"].nunique() if "customer_id" in sample else 0,
        }
    if (
        sample["outcome_mature"].isna().any()
        or not sample["outcome_mature"].isin([True, False]).all()
    ):
        return {
            "status": "not_estimable",
            "reason": "invalid_input:outcome_maturity_flag",
            "failure_category": "invalid_input",
            "n": len(sample),
            "clusters": sample["customer_id"].nunique(),
        }
    mature = sample["outcome_mature"].astype(bool)
    censored = int((~mature).sum())
    frame = (
        sample[mature]
        .dropna(
            subset=[
                "retained_next_12m",
                "prior_12m_orders",
                "segment_code",
                "home_country_code",
                "tenure_months",
            ]
        )
        .copy()
    )
    landmark_dates = pd.to_datetime(frame["landmark_month"], errors="coerce")
    invalid = {
        "missing_customer_or_landmark": int(
            (frame["customer_id"].isna() | landmark_dates.isna()).sum()
        ),
        "duplicate_customer_landmark": int(
            frame.duplicated(["customer_id", "landmark_month"]).sum()
        ),
        "nonbinary_outcome": int((~frame["retained_next_12m"].isin([0, 1])).sum()),
        "negative_prior_orders": int((frame["prior_12m_orders"] < 0).sum()),
        "negative_tenure": int((frame["tenure_months"] < 0).sum()),
    }
    if any(invalid.values()):
        return {
            "status": "not_estimable",
            "reason": "invalid_input:retention_sample",
            "failure_category": "invalid_input",
            "invalid_counts": invalid,
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
        }
    frame["landmark_year"] = landmark_dates.dt.year.astype(str)
    segments = sorted(frame["segment_code"].unique())
    segment_clusters = frame.groupby("segment_code")["customer_id"].nunique().to_dict()
    if (
        len(segments) < 2
        or frame.customer_id.nunique() < 30
        or len(frame) < 100
        or frame.retained_next_12m.nunique() < 2
    ):
        return {
            "status": "insufficient_evidence",
            "reason": "small_or_single_class_sample",
            "failure_category": "insufficient_data",
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
        }
    if any(count < 20 for count in segment_clusters.values()):
        return {
            "status": "insufficient_evidence",
            "reason": "sparse_segment",
            "failure_category": "insufficient_data",
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "segment_clusters": segment_clusters,
            "censored": censored,
        }
    separated = {}
    for segment, group in frame.groupby("segment_code", sort=True):
        retained = int((group["retained_next_12m"] == 1).sum())
        not_retained = len(group) - retained
        if retained == 0 or not_retained == 0:
            separated[segment] = {"retained": retained, "not_retained": not_retained}
    if separated:
        return {
            "status": "not_estimable",
            "reason": "complete_separation:" + ",".join(separated),
            "failure_category": "nonidentifiable_model",
            "separated_segments": separated,
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
            "missing": int(mature.sum()) - len(frame),
        }
    formula = (
        "retained_next_12m ~ C(segment_code) + np.log1p(prior_12m_orders) "
        "+ tenure_months + C(home_country_code) + C(landmark_year)"
    )
    try:
        model = smf.glm(formula, data=frame, family=sm.families.Binomial())
        rank = np.linalg.matrix_rank(model.exog)
        if rank != model.exog.shape[1]:
            return {
                "status": "not_estimable",
                "reason": "collinear_controls",
                "failure_category": "nonidentifiable_model",
                "n": len(frame),
                "clusters": frame.customer_id.nunique(),
                "censored": censored,
            }
        fit = model.fit(cov_type="cluster", cov_kwds={"groups": frame.customer_id})
        if not fit.converged or not np.isfinite(fit.params.to_numpy()).all():
            return {
                "status": "not_estimable",
                "reason": "numerical_failure:nonconverged_or_nonfinite_fit",
                "failure_category": "numerical_failure",
                "n": len(frame),
                "clusters": frame.customer_id.nunique(),
                "censored": censored,
            }
        covariance = fit.cov_params().to_numpy()
        if not np.isfinite(covariance).all():
            return {
                "status": "not_estimable",
                "reason": "numerical_failure:nonfinite_cluster_covariance",
                "failure_category": "numerical_failure",
                "n": len(frame),
                "clusters": frame.customer_id.nunique(),
                "censored": censored,
            }
        eigenvalues, vectors = np.linalg.eigh(covariance)
        if np.min(eigenvalues) < -1e-7:
            return {
                "status": "not_estimable",
                "reason": "numerical_failure:indefinite_cluster_covariance",
                "failure_category": "numerical_failure",
                "minimum_covariance_eigenvalue": float(np.min(eigenvalues)),
                "n": len(frame),
                "clusters": frame.customer_id.nunique(),
                "censored": censored,
            }
        transform = vectors @ np.diag(np.sqrt(np.clip(eigenvalues, 0, None)))
        rng = np.random.default_rng(seed)
        parameters = fit.params.to_numpy()[:, None] + transform @ rng.standard_normal(
            (len(fit.params), draws)
        )
        rates: dict[str, float] = {}
        simulated: dict[str, np.ndarray] = {}
        for segment in segments:
            standardized = frame.copy()
            standardized["segment_code"] = segment
            design = np.asarray(
                patsy.build_design_matrices([model.data.model_spec], standardized)[0]
            )
            rates[segment] = float(expit(design @ fit.params.to_numpy()).mean())
            simulated[segment] = np.concatenate(
                [
                    expit(design @ parameters[:, start : start + 40]).mean(axis=0)
                    for start in range(0, draws, 40)
                ]
            )
        baseline = segments[0]
        contrasts = []
        for segment in segments[1:]:
            difference = (simulated[segment] - simulated[baseline]) * 100
            estimate = (rates[segment] - rates[baseline]) * 100
            se = float(np.std(difference, ddof=1))
            contrasts.append(
                {
                    "segment": segment,
                    "baseline": baseline,
                    "estimate": estimate,
                    "ci_low": float(np.quantile(difference, 0.025)),
                    "ci_high": float(np.quantile(difference, 0.975)),
                    "p_value": float(2 * norm.sf(abs(estimate / se))) if se > 0 else None,
                    "segment_clusters": segment_clusters[segment],
                }
            )
        q = bh_adjust([x["p_value"] if x["p_value"] is not None else 1.0 for x in contrasts])
        for row, value in zip(contrasts, q, strict=True):
            row["q_value"] = value
        return {
            "status": "association_only",
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
            "missing": int(mature.sum()) - len(frame),
            "segment_clusters": segment_clusters,
            "standardized_rates": rates,
            "contrasts": contrasts,
            "seed": seed,
            "draws": draws,
            "rank": int(rank),
            "condition_number": float(np.linalg.cond(model.exog)),
            "max_predicted_probability": float(np.max(fit.predict(frame))),
            "min_predicted_probability": float(np.min(fit.predict(frame))),
        }
    except PerfectSeparationError:
        return {
            "status": "not_estimable",
            "reason": "complete_separation:glm_detected",
            "failure_category": "nonidentifiable_model",
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
        }
    except np.linalg.LinAlgError as exc:
        return {
            "status": "not_estimable",
            "reason": f"numerical_failure:{type(exc).__name__}",
            "failure_category": "numerical_failure",
            "n": len(frame),
            "clusters": frame.customer_id.nunique(),
            "censored": censored,
        }


def basket_associations(
    membership: pd.DataFrame, rules: pd.DataFrame, *, seed: int = SEED, replicates: int = 400
) -> dict[str, Any]:
    """Cluster bootstrap of popularity-adjusted subcategory lift, with BH correction."""
    unique = membership.drop_duplicates(["order_id", "subcategory_id"])
    if rules.duplicated(["subcategory_a_id", "subcategory_b_id"]).any():
        raise ValueError("duplicate Product basket rule")
    if unique.empty:
        return {
            "status": "insufficient_evidence",
            "reason": "no_fulfilled_baskets",
            "basket_count": 0,
            "clusters": 0,
        }
    if unique.groupby("order_id")["customer_id"].nunique().max() != 1:
        raise ValueError("basket has multiple customers")
    baskets = (
        unique.groupby("order_id", sort=True)
        .agg(customer_id=("customer_id", "first"), items=("subcategory_id", frozenset))
        .reset_index()
    )
    n = len(baskets)
    customer_codes, customers = pd.factorize(baskets.customer_id, sort=True)
    clusters = len(customers)
    if not n or clusters < 20:
        return {
            "status": "insufficient_evidence",
            "reason": "too_few_customer_clusters",
            "basket_count": n,
            "clusters": clusters,
        }
    if rules.empty:
        return {
            "status": "insufficient_evidence",
            "reason": "no_eligible_product_pairs",
            "basket_count": n,
            "clusters": clusters,
        }
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(clusters, np.full(clusters, 1 / clusters), size=replicates)
    rows = []
    for rule in rules.sort_values(["subcategory_a_id", "subcategory_b_id"]).itertuples():
        a = np.fromiter((rule.subcategory_a_id in items for items in baskets["items"]), bool, n)
        b = np.fromiter((rule.subcategory_b_id in items for items in baskets["items"]), bool, n)
        na, nb, nab = int(a.sum()), int(b.sum()), int((a & b).sum())
        if (n, na, nb, nab) != (
            rule.basket_count,
            rule.item_a_baskets,
            rule.item_b_baskets,
            rule.joint_baskets,
        ):
            raise ValueError("product basket counts do not reconcile to Warehouse")
        cell = np.column_stack((a & b, a & ~b, ~a & b, ~a & ~b)).astype(int)
        by_customer = np.stack(
            [np.bincount(customer_codes, weights=cell[:, i], minlength=clusters) for i in range(4)],
            axis=1,
        )
        draw = weights @ by_customer
        total = draw.sum(axis=1)
        item_a = draw[:, 0] + draw[:, 1]
        item_b = draw[:, 0] + draw[:, 2]
        valid = (draw[:, 0] > 0) & (item_a > 0) & (item_b > 0)
        if int(valid.sum()) < replicates * 0.9:
            rows.append(
                {
                    "item_a": rule.subcategory_a_id,
                    "item_b": rule.subcategory_b_id,
                    "status": "insufficient_evidence",
                    "reason": "unstable_cluster_bootstrap",
                    "joint_baskets": nab,
                    "customer_clusters": clusters,
                }
            )
            continue
        lifts = draw[valid, 0] * total[valid] / (item_a[valid] * item_b[valid])
        lift = nab * n / (na * nb)
        se = float(np.std(np.log(lifts), ddof=1))
        p = float(2 * norm.sf(abs(np.log(lift) / se))) if se > 0 else None
        low, high = np.quantile(lifts, [0.025, 0.975])
        rows.append(
            {
                "item_a": rule.subcategory_a_id,
                "item_b": rule.subcategory_b_id,
                "status": "association_only",
                "basket_count": n,
                "customer_clusters": clusters,
                "item_a_baskets": na,
                "item_b_baskets": nb,
                "joint_baskets": nab,
                "pair_support": nab / n,
                "confidence_a_to_b": nab / na,
                "confidence_b_to_a": nab / nb,
                "lift": lift,
                "expected_joint_baskets": na * nb / n,
                "ci_low": float(low),
                "ci_high": float(high),
                "p_value": p,
                "bootstrap_valid_replicates": int(valid.sum()),
            }
        )
    indices = [i for i, x in enumerate(rows) if x["status"] == "association_only"]
    q = bh_adjust([rows[i]["p_value"] if rows[i]["p_value"] is not None else 1.0 for i in indices])
    for i, value in zip(indices, q, strict=True):
        rows[i]["q_value"] = value
    return {
        "status": "association_only",
        "rows": rows,
        "basket_count": n,
        "clusters": clusters,
        "pairs_tested": len(indices),
        "seed": seed,
        "replicates": replicates,
    }
