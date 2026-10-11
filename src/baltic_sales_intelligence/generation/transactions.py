"""Deterministic synthetic orders, returns and target allocations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import numpy as np
import pandas as pd

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.financial import (
    RATE,
    UNIT,
    ZERO,
    quantize_cent,
    quantize_unit,
    return_postings,
    sale_postings,
)
from baltic_sales_intelligence.generation.reference_data import CITIES, PUBLIC_COLUMNS
from baltic_sales_intelligence.generation.scenario_controls import (
    ScenarioControl,
    active,
    load_scenario_controls,
    progress,
    resolve_targets,
    scenario_rng,
)

_PAYMENT_TERMS = ("prepaid", "net_14", "net_30", "net_45")
_RETURN_REASONS = (
    "defective",
    "wrong_item",
    "damaged_in_transit",
    "ordered_in_error",
    "quality_issue",
    "not_as_expected",
    "duplicate_order",
)
_MONTH_FACTORS = (0.82, 0.88, 0.98, 1.02, 1.05, 1.00, 0.91, 0.86, 1.03, 1.10, 1.17, 1.08)


def _rng(configuration: ProjectConfiguration, component: str) -> np.random.Generator:
    component_id = getattr(configuration.generation.component_ids, component)
    sequence = np.random.SeedSequence(
        configuration.generation.master_seed, spawn_key=(component_id, 0)
    )
    return np.random.Generator(np.random.PCG64(sequence))


def _decimal(value: float, quantum: Decimal = UNIT) -> Decimal:
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)


def _s01_retained_probability(control: ScenarioControl, event_date: date) -> float:
    """Return the smooth convex retention probability for the S01 customer."""

    terminal = float(control.strength["terminal_intensity_multiplier"])
    exponent = float(control.strength["decay_exponent"])
    return terminal + (1.0 - terminal) * (1.0 - progress(control, event_date)) ** exponent


def _index_from_uniform(candidates: np.ndarray, draw: float) -> int:
    """Select uniformly from candidates using a previously consumed component draw."""

    return int(candidates[min(int(draw * len(candidates)), len(candidates) - 1)])


def _apply_s14_demand_weight(
    weights: np.ndarray,
    categories: np.ndarray,
    target: str,
    control: ScenarioControl,
    event_date: date,
) -> None:
    """Apply S14 only to transactions recognised in its fulfilment window."""

    if active(control, event_date):
        weights[categories == target] *= float(control.strength["demand_multiplier"])


def _apply_s09_pairing(
    selected_indexes: np.ndarray,
    eligible_categories: np.ndarray,
    pair_a: str,
    pair_b: str,
    pairing_probability: float,
    affinity_draw: float,
    replacement_draw: float,
) -> None:
    """Apply S09 pairing using draws already consumed from its component stream."""

    selected_categories = set(eligible_categories[selected_indexes])
    if (
        len(selected_indexes) < 2
        or len({pair_a, pair_b} & selected_categories) != 1
        or affinity_draw >= pairing_probability
    ):
        return
    missing_category = pair_b if pair_a in selected_categories else pair_a
    candidates = np.flatnonzero(eligible_categories == missing_category)
    replaceable = [
        index
        for index, selected in enumerate(selected_indexes)
        if eligible_categories[selected] not in {pair_a, pair_b}
    ]
    if len(candidates) and replaceable:
        selected_indexes[replaceable[-1]] = _index_from_uniform(candidates, replacement_draw)


def _random_date(rng: np.random.Generator, start: date, end: date) -> date:
    return start + timedelta(days=int(rng.integers((end - start).days + 1)))


def _history_resolver(history: pd.DataFrame) -> Callable[[str, date], Any]:
    records = {
        customer_id: tuple(rows.sort_values("valid_from").itertuples(index=False))
        for customer_id, rows in history.groupby("customer_id", sort=False)
    }

    def resolve(customer_id: str, event_date: date) -> Any:
        for row in records[customer_id]:
            if row.valid_from <= event_date and (row.valid_to is None or event_date < row.valid_to):
                return row
        raise ValueError(f"no customer history for {customer_id} on {event_date}")

    return resolve


def _eligible_salespeople(salespeople: pd.DataFrame, event_date: date) -> list[Any]:
    return [
        row
        for row in salespeople.itertuples(index=False)
        if row.hire_date <= event_date
        and (row.termination_date is None or event_date < row.termination_date)
    ]


def generate_orders_and_lines(
    configuration: ProjectConfiguration,
    tables: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate order headers and immutable historical line snapshots."""

    rng = _rng(configuration, "orders")
    settings = configuration.generation.transactions
    reporting = configuration.application.reporting
    controls = {control.story_id: control for control in load_scenario_controls()}
    targets = resolve_targets(tuple(controls.values()), tables)
    story_rngs = {
        story_id: scenario_rng(configuration.generation.master_seed, control)
        for story_id, control in controls.items()
    }
    customers = list(tables["customers"].itertuples(index=False))
    products = list(tables["products"].itertuples(index=False))
    subcategory_category = (
        tables["product_subcategories"].set_index("subcategory_id")["category_id"].to_dict()
    )
    resolve_history = _history_resolver(tables["customer_attribute_history"])
    city_ids = tuple(row[0] for row in CITIES)
    cities_by_country = {
        country: tuple(row[0] for row in CITIES if row[2] == country)
        for country in ("LT", "LV", "EE")
    }
    channel_codes = tuple(tables["sales_channels"]["channel_code"])
    people = tables["salespeople"]

    initial_history = (
        tables["customer_attribute_history"]
        .sort_values("valid_from")
        .groupby("customer_id", sort=False)
        .first()
    )
    size_factor = {"micro": 0.55, "small": 0.9, "medium": 1.5, "large": 2.6}
    segment_factor = {
        "SMALL_BUSINESS": 0.7,
        "MID_MARKET": 1.15,
        "ENTERPRISE": 2.0,
        "PUBLIC_SECTOR": 1.05,
        "TRADE_RESELLER": 1.35,
    }
    customer_weights = np.array(
        [
            size_factor[initial_history.loc[row.customer_id, "company_size_band"]]
            * segment_factor[initial_history.loc[row.customer_id, "segment_code"]]
            * max(
                0.20,
                (reporting.period_end - max(row.acquisition_date, reporting.period_start)).days
                / 1734,
            )
            for row in customers
        ]
    )
    concentration_ids = set(targets["S10"].split("+"))
    customer_weights *= np.array(
        [
            float(controls["S10"].strength["purchase_weight_multiplier"])
            if row.customer_id in concentration_ids
            else 1.0
            for row in customers
        ]
    )
    customer_weights /= customer_weights.sum()

    order_rows: list[tuple[Any, ...]] = []
    line_rows: list[tuple[Any, ...]] = []
    line_number_global = 0
    for order_index in range(1, configuration.generation.population.orders_target + 1):
        customer = customers[int(rng.choice(len(customers), p=customer_weights))]
        first_date = max(customer.acquisition_date, reporting.period_start)
        # A mild growth curve, then customer eligibility, gives uneven exposure without schedules.
        u = float(rng.random()) ** 0.82
        order_date = first_date + timedelta(
            days=round(u * (reporting.period_end - first_date).days)
        )
        # S01 decays ordinary selection probability smoothly.  Rejected target
        # draws are reassigned through the same customer distribution.
        churn_control = controls["S01"]
        if customer.customer_id == targets["S01"] and active(churn_control, order_date):
            retained_probability = _s01_retained_probability(churn_control, order_date)
            if story_rngs["S01"].random() > retained_probability:
                eligible_indexes = np.array(
                    [
                        index
                        for index, candidate in enumerate(customers)
                        if candidate.customer_id != targets["S01"]
                        and candidate.acquisition_date <= order_date
                    ]
                )
                eligible_weights = customer_weights[eligible_indexes]
                eligible_weights /= eligible_weights.sum()
                customer = customers[
                    int(story_rngs["S01"].choice(eligible_indexes, p=eligible_weights))
                ]
        # S15 reduces the incidence of incumbent LV purchases, not merely the
        # units on orders that were already selected.  Released demand is
        # reassigned to genuinely acquired LV customers where exposure exists.
        health_control = controls["S15"]
        customer_initial = initial_history.loc[customer.customer_id]
        if (
            customer_initial["home_country_code"] == "LV"
            and customer.acquisition_date < health_control.start_date
            and active(health_control, order_date)
            and story_rngs["S15"].random()
            > float(health_control.strength["incumbent_demand_multiplier"])
        ):
            eligible_indexes = np.array(
                [
                    index
                    for index, candidate in enumerate(customers)
                    if health_control.start_date <= candidate.acquisition_date <= order_date
                    and initial_history.loc[candidate.customer_id, "home_country_code"] == "LV"
                ]
            )
            if len(eligible_indexes):
                eligible_weights = customer_weights[eligible_indexes]
                eligible_weights /= eligible_weights.sum()
                customer = customers[
                    int(story_rngs["S15"].choice(eligible_indexes, p=eligible_weights))
                ]
        status = str(rng.choice(("placed", "fulfilled", "cancelled"), p=settings.status_weights))
        fulfilled_date = None
        cancelled_date = None
        if status == "fulfilled":
            fulfilled_date = min(
                order_date + timedelta(days=int(rng.integers(0, 12))), reporting.extension_end
            )
        elif status == "cancelled":
            cancelled_date = min(
                order_date + timedelta(days=int(rng.integers(0, 8))), reporting.extension_end
            )
        credit_date = fulfilled_date or order_date
        home = resolve_history(customer.customer_id, order_date)
        channel = str(rng.choice(channel_codes, p=(0.31, 0.23, 0.18, 0.20, 0.08)))
        if rng.random() < settings.cross_border_share:
            ship_options = tuple(
                city for city in city_ids if city not in cities_by_country[home.home_country_code]
            )
            ship_city = str(rng.choice(ship_options))
        elif rng.random() < 0.78:
            ship_city = home.home_city_id
        else:
            ship_city = str(rng.choice(cities_by_country[home.home_country_code]))
        eligible_people = _eligible_salespeople(people, credit_date)
        credited = None
        credit_probability = 1 - settings.uncredited_share
        if channel in {"WEB", "TRADE_COUNTER"}:
            credit_probability -= 0.22
        if eligible_people and rng.random() < max(0.35, credit_probability):
            same_market = [
                p for p in eligible_people if p.primary_country_code == home.home_country_code
            ]
            pool = same_market if same_market and rng.random() < 0.88 else eligible_people
            credited = pool[int(rng.integers(len(pool)))].salesperson_id

        high_volume_rep = targets["S04"]
        if (
            home.home_country_code == "LT"
            and active(controls["S04"], credit_date)
            and any(person.salesperson_id == high_volume_rep for person in eligible_people)
            and story_rngs["S04"].random() < float(controls["S04"].strength["credit_affinity"])
        ):
            credited = high_volume_rep
        high_margin_rep = targets["S05"]
        portfolio_modulus = int(controls["S05"].strength["portfolio_modulus"])
        loyal_portfolio = int(customer.customer_id[-5:]) % portfolio_modulus == 0
        # Remove incidental one-off credit to the target outside its stable
        # book; ordinary LV peers remain eligible and credit can remain null.
        if (
            active(controls["S05"], credit_date)
            and credited == high_margin_rep
            and not (home.home_country_code == "LV" and loyal_portfolio)
        ):
            replacement_pool = [
                person
                for person in eligible_people
                if person.salesperson_id != high_margin_rep
                and person.primary_country_code == home.home_country_code
            ]
            credited = (
                replacement_pool[
                    int(story_rngs["S05"].integers(len(replacement_pool)))
                ].salesperson_id
                if replacement_pool
                else None
            )
        if (
            home.home_country_code == "LV"
            and loyal_portfolio
            and active(controls["S05"], credit_date)
            and any(person.salesperson_id == high_margin_rep for person in eligible_people)
            and story_rngs["S05"].random() < float(controls["S05"].strength["portfolio_affinity"])
        ):
            credited = high_margin_rep

        count = int(rng.choice(np.arange(1, 7), p=settings.line_count_weights))
        event_date = fulfilled_date or order_date
        eligible_products = [
            product
            for product in products
            if product.launch_date <= order_date
            and product.launch_date <= event_date
            and (product.discontinued_date is None or event_date < product.discontinued_date)
        ]
        if len(eligible_products) < count:
            raise ValueError(f"insufficient eligible products for order {order_index}")
        eligible_categories = np.array(
            [subcategory_category[product.subcategory_id] for product in eligible_products],
            dtype=object,
        )
        selection_weights = np.ones(len(eligible_products), dtype=float)
        if active(controls["S06"], order_date):
            selection_weights[eligible_categories == targets["S06"]] *= float(
                controls["S06"].strength["demand_multiplier"]
            )
        if active(controls["S08"], order_date) and order_date.month in set(
            controls["S08"].strength["peak_months"]
        ):
            selection_weights[eligible_categories == targets["S08"]] *= float(
                controls["S08"].strength["demand_multiplier"]
            )
        if active(controls["S11"], order_date):
            selection_weights[eligible_categories == targets["S11"]] *= float(
                controls["S11"].strength["demand_multiplier"]
            )
        if active(controls["S13"], order_date):
            selection_weights[eligible_categories == targets["S13"]] *= float(
                controls["S13"].strength["demand_multiplier"]
            )
        s14_active = active(controls["S14"], event_date)
        _apply_s14_demand_weight(
            selection_weights,
            eligible_categories,
            targets["S14"],
            controls["S14"],
            event_date,
        )
        selection_weights /= selection_weights.sum()
        selection_rng = story_rngs["S14"] if s14_active else rng
        selected_indexes = np.asarray(
            selection_rng.choice(
                len(eligible_products), size=count, replace=False, p=selection_weights
            )
        )
        pair_a, pair_b = targets["S09"].split("+")
        s09_affinity_draw = story_rngs["S09"].random()
        s09_replacement_draw = story_rngs["S09"].random()
        if active(controls["S09"], order_date):
            _apply_s09_pairing(
                selected_indexes,
                eligible_categories,
                pair_a,
                pair_b,
                float(controls["S09"].strength["pairing_probability"]),
                s09_affinity_draw,
                s09_replacement_draw,
            )
        order_line_values: list[Decimal] = []
        order_quantities: list[int] = []
        for local_line, selected_index in enumerate(selected_indexes, 1):
            product = eligible_products[int(selected_index)]
            category_id = subcategory_category[product.subcategory_id]
            category_number = int(category_id[-2:])
            years = max(0.0, (order_date - date(2022, 1, 1)).days / 365.25)
            category_drift = 0.004 * ((category_number % 5) - 2)
            list_price = quantize_unit(
                product.reference_list_unit_price_eur
                * _decimal((1.020 + category_drift) ** (years - 4.75), Decimal("0.00000001"))
            )
            list_price = max(Decimal("0.0100"), list_price)
            base_cost = product.reference_standard_unit_cost_eur
            cost_growth = 1.030 + (0.012 if home.home_country_code == "EE" else 0.0)
            unit_cost = quantize_unit(
                base_cost * _decimal(cost_growth ** (years - 4.75), Decimal("0.00000001"))
            )
            unit_cost = max(Decimal("0.0100"), unit_cost)
            discount_base = {"micro": 0.025, "small": 0.055, "medium": 0.105, "large": 0.155}[
                home.company_size_band
            ]
            if channel == "ACCOUNT_MANAGER":
                discount_base += 0.025
            discount = max(0.0, min(0.32, discount_base + float(rng.normal(0, 0.025))))

            if home.home_country_code == "EE" and active(controls["S02"], order_date):
                unit_cost = quantize_unit(
                    unit_cost * _decimal(float(controls["S02"].strength["cost_multiplier"]))
                )
                discount += float(controls["S02"].strength["discount_addition"])
            if ship_city == targets["S03"] and active(controls["S03"], order_date):
                discount += float(controls["S03"].strength["discount_addition"])
            if credited == targets["S04"] and active(controls["S04"], order_date):
                discount += float(controls["S04"].strength["discount_addition"])
            if credited == targets["S05"] and active(controls["S05"], order_date):
                discount += float(controls["S05"].strength["discount_addition"])
            if home.segment_code == targets["S12"] and active(controls["S12"], order_date):
                discount += float(controls["S12"].strength["discount_addition"])
                unit_cost = quantize_unit(
                    unit_cost * _decimal(float(controls["S12"].strength["cost_multiplier"]))
                )
            if category_id == targets["S11"] and active(controls["S11"], order_date):
                list_price = quantize_unit(
                    list_price * _decimal(float(controls["S11"].strength["list_price_multiplier"]))
                )
            if category_id == targets["S13"] and active(controls["S13"], order_date):
                list_price = quantize_unit(
                    list_price * _decimal(float(controls["S13"].strength["list_price_multiplier"]))
                )
                unit_cost = quantize_unit(
                    unit_cost * _decimal(float(controls["S13"].strength["cost_multiplier"]))
                )
            if home.home_country_code == "LV" and active(controls["S15"], order_date):
                unit_cost = quantize_unit(
                    unit_cost * _decimal(float(controls["S15"].strength["cost_multiplier"]))
                )
                discount += float(controls["S15"].strength["discount_addition"])
            discount = max(0.0, min(0.45, discount))
            discount_rate = _decimal(discount, RATE)
            realised = quantize_unit(list_price * (Decimal("1") - discount_rate))
            quantity = int(rng.choice(np.arange(1, 9), p=settings.quantity_weights))
            if home.company_size_band == "large" and rng.random() < 0.25:
                quantity += int(rng.integers(4, 18))
            quantity_multiplier = 1.0
            if home.home_country_code == "EE" and active(controls["S02"], order_date):
                quantity_multiplier *= float(controls["S02"].strength["demand_multiplier"])
            if credited == targets["S04"] and active(controls["S04"], order_date):
                quantity_multiplier *= float(controls["S04"].strength["quantity_multiplier"])
            if credited == targets["S05"] and active(controls["S05"], order_date):
                quantity_multiplier *= float(controls["S05"].strength["quantity_multiplier"])
            if home.segment_code == targets["S12"] and active(controls["S12"], order_date):
                quantity_multiplier *= float(controls["S12"].strength["demand_multiplier"])
            if home.home_country_code == "LV" and active(controls["S15"], order_date):
                is_new = customer.acquisition_date >= controls["S15"].start_date
                key = "new_customer_demand_multiplier" if is_new else "incumbent_demand_multiplier"
                quantity_multiplier *= float(controls["S15"].strength[key])
            quantity = max(1, round(quantity * quantity_multiplier))
            vat_rate = (
                Decimal("0.000000")
                if rng.random() < settings.zero_vat_share
                else Decimal("0.200000")
            )
            vat_treatment = "sim_zero" if vat_rate == 0 else "sim_standard"
            postings = sale_postings(
                quantity,
                list_price,
                discount_rate,
                unit_cost,
                vat_rate,
                fulfilled=status == "fulfilled",
            )
            line_number_global += 1
            line_rows.append(
                (
                    f"OL-{line_number_global:07d}",
                    f"ORD-{order_index:07d}",
                    local_line,
                    product.product_id,
                    quantity,
                    list_price,
                    discount_rate,
                    realised,
                    unit_cost,
                    postings[0],
                    postings[1],
                    postings[2],
                    postings[3],
                    home.home_country_code,
                    vat_treatment,
                    vat_rate,
                    postings[4],
                    postings[5],
                    postings[6],
                )
            )
            order_line_values.append(postings[2])
            order_quantities.append(quantity)
        if status == "fulfilled":
            base_freight = (
                3.5 + 0.006 * float(sum(order_line_values, ZERO)) + 0.12 * sum(order_quantities)
            )
            outbound_freight = quantize_cent(_decimal(max(0, base_freight + rng.normal(0, 1.2))))
        else:
            outbound_freight = ZERO
        order_rows.append(
            (
                f"ORD-{order_index:07d}",
                customer.customer_id,
                order_date,
                fulfilled_date,
                cancelled_date,
                channel,
                status,
                ship_city,
                credited,
                str(rng.choice(_PAYMENT_TERMS, p=(0.08, 0.22, 0.58, 0.12))),
                outbound_freight,
            )
        )

    return (
        pd.DataFrame(order_rows, columns=PUBLIC_COLUMNS["orders"]),
        pd.DataFrame(line_rows, columns=PUBLIC_COLUMNS["order_lines"]),
    )


def generate_returns(
    configuration: ProjectConfiguration,
    orders: pd.DataFrame,
    lines: pd.DataFrame,
    products: pd.DataFrame | None = None,
    subcategories: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Generate lifecycle events with cumulative accepted quantities capped at sold units."""

    rng = _rng(configuration, "returns")
    settings = configuration.generation.transactions
    reporting = configuration.application.reporting
    controls = {control.story_id: control for control in load_scenario_controls()}
    fulfilled = orders[orders["order_status"] == "fulfilled"].set_index("order_id")
    eligible = lines[lines["order_id"].isin(fulfilled.index)].reset_index(drop=True)
    target = (
        configuration.generation.population.return_events_min
        + configuration.generation.population.return_events_max
    ) // 2
    if target == 0:
        empty_returns = pd.DataFrame(columns=PUBLIC_COLUMNS["returns"])
        empty_returns["accepted_quantity"] = pd.Series(dtype="Int64")
        return empty_returns
    category_bias = np.array(
        [1.0 + (int(value[-4:]) % 7) * 0.08 for value in eligible["product_id"]]
    )
    if products is not None and subcategories is not None:
        product_category = (
            products[["product_id", "subcategory_id"]]
            .merge(
                subcategories[["subcategory_id", "category_id"]],
                on="subcategory_id",
                validate="many_to_one",
            )
            .set_index("product_id")["category_id"]
        )
        fulfilled_dates = eligible["order_id"].map(fulfilled["fulfilled_date"])
        return_control = controls["S06"]
        category_bias *= np.array(
            [
                float(return_control.strength["return_weight_multiplier"])
                if product_category[product_id] == return_control.target["id"]
                and active(return_control, fulfilled_date)
                else 1.0
                for product_id, fulfilled_date in zip(
                    eligible["product_id"], fulfilled_dates, strict=True
                )
            ]
        )
    category_bias /= category_bias.sum()
    accepted_so_far: dict[str, int] = {}
    rows: list[tuple[Any, ...]] = []
    for number in range(1, target + 1):
        if number == target:
            fulfilment_dates = eligible["order_id"].map(fulfilled["fulfilled_date"])
            line = eligible.loc[fulfilment_dates.idxmax()]
        else:
            line = eligible.iloc[int(rng.choice(len(eligible), p=category_bias))]
        sold = int(line["quantity_ordered"])
        status = str(
            rng.choice(("requested", "accepted", "rejected"), p=settings.return_status_weights)
        )
        if number == target and status == "requested":
            status = "rejected"
        remaining = sold - accepted_so_far.get(line["order_line_id"], 0)
        if status == "accepted" and remaining <= 0:
            status = "rejected"
        requested_quantity = int(rng.integers(1, sold + 1))
        accepted_quantity = None
        if status == "accepted":
            accepted_quantity = int(rng.integers(1, min(requested_quantity, remaining) + 1))
            accepted_so_far[line["order_line_id"]] = (
                accepted_so_far.get(line["order_line_id"], 0) + accepted_quantity
            )
        fulfilment = fulfilled.loc[line["order_id"], "fulfilled_date"]
        request_date = min(
            fulfilment + timedelta(days=int(rng.integers(1, 76))), reporting.extension_end
        )
        if number == target:
            request_date = max(
                request_date,
                reporting.default_as_of_date + timedelta(days=1),
            )
        decision_date = (
            None
            if status == "requested"
            else min(
                request_date + timedelta(days=int(rng.integers(0, 15))), reporting.extension_end
            )
        )
        restocking = None
        refund_unit = None
        if status == "accepted":
            restocking = (
                "restockable" if rng.random() < settings.restockable_share else "written_off"
            )
            refund_factor = _decimal(float(rng.uniform(0.88, 1.0)), Decimal("0.000001"))
            refund_unit = quantize_unit(line["realised_unit_price_eur"] * refund_factor)
        refund, recovered, vat_reversal = return_postings(
            accepted_quantity,
            refund_unit,
            line["realised_unit_price_eur"],
            line["unit_cost_eur"],
            line["vat_rate_fraction"],
            restocking,
            accepted=status == "accepted",
        )
        return_freight = (
            quantize_cent(_decimal(max(0, float(rng.normal(5.5, 2.0)))))
            if status == "accepted" and rng.random() < 0.65
            else ZERO
        )
        rows.append(
            (
                f"RET-{number:06d}",
                line["order_line_id"],
                request_date,
                decision_date,
                requested_quantity,
                accepted_quantity,
                status,
                str(rng.choice(_RETURN_REASONS)),
                restocking,
                refund_unit,
                refund,
                recovered,
                vat_reversal,
                return_freight,
            )
        )
    returns = pd.DataFrame(rows, columns=PUBLIC_COLUMNS["returns"])
    returns["accepted_quantity"] = returns["accepted_quantity"].astype("Int64")
    return returns


def _month_starts(start: date, end: date) -> list[date]:
    result = []
    current = start.replace(day=1)
    while current <= end:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return result


def generate_targets(
    configuration: ProjectConfiguration,
    salespeople: pd.DataFrame,
) -> pd.DataFrame:
    """Generate independent leaf-level monthly plan allocations."""

    rng = _rng(configuration, "targets")
    settings = configuration.generation.transactions
    reporting = configuration.application.reporting
    rows: list[tuple[Any, ...]] = []
    target_number = 0
    country_base = {"LT": Decimal("1550000"), "LV": Decimal("780000"), "EE": Decimal("540000")}
    for month in _month_starts(reporting.period_start, reporting.period_end):
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        growth = Decimal(str((1 + settings.target_annual_growth) ** (month.year - 2022)))
        seasonal = Decimal(str(_MONTH_FACTORS[month.month - 1]))
        eligible = salespeople[
            salespeople["hire_date"].lt(next_month)
            & (salespeople["termination_date"].isna() | salespeople["termination_date"].gt(month))
        ]
        for metric, metric_factor in (
            ("net_revenue_eur", Decimal("1")),
            ("gross_profit_eur", Decimal("0.31")),
        ):
            for country in ("LT", "LV", "EE"):
                market_plan = quantize_cent(
                    country_base[country] * growth * seasonal * metric_factor
                )
                unallocated = quantize_cent(
                    market_plan * Decimal(str(settings.unallocated_target_share))
                )
                target_number += 1
                rows.append(
                    (
                        f"TGT-{target_number:06d}",
                        month,
                        metric,
                        "unallocated_country",
                        None,
                        country,
                        unallocated,
                    )
                )
                country_people = eligible[eligible["primary_country_code"] == country]
                if country_people.empty:
                    continue
                available = market_plan - unallocated
                weights = rng.lognormal(0, 0.28, len(country_people))
                weights /= weights.sum()
                allocated = ZERO
                records = list(country_people.itertuples(index=False))
                for index, person in enumerate(records):
                    value = (
                        quantize_cent(market_plan - unallocated - allocated)
                        if index == len(records) - 1
                        else quantize_cent(available * Decimal(str(weights[index])))
                    )
                    allocated += value
                    target_number += 1
                    rows.append(
                        (
                            f"TGT-{target_number:06d}",
                            month,
                            metric,
                            "salesperson",
                            person.salesperson_id,
                            country,
                            value,
                        )
                    )
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["sales_targets"])


def generate_transaction_tables(
    configuration: ProjectConfiguration,
    tables: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    orders, lines = generate_orders_and_lines(configuration, tables)
    return {
        "orders": orders,
        "order_lines": lines,
        "returns": generate_returns(
            configuration,
            orders,
            lines,
            tables["products"],
            tables["product_subcategories"],
        ),
        "sales_targets": generate_targets(configuration, tables["salespeople"]),
    }
