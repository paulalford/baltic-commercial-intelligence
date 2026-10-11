"""Deterministic generation and atomic publication of Milestone 2 reference truth."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import (
    ACCOUNT_STATUSES,
    CHANNELS,
    CITIES,
    COMPANY_SIZE_BANDS,
    COUNTRIES,
    CREDIT_TIERS,
    INDUSTRIES,
    PUBLIC_COLUMNS,
    REGIONS,
    SEGMENTS,
)
from baltic_sales_intelligence.generation.scenario_controls import (
    controls_hash,
    load_scenario_controls,
)
from baltic_sales_intelligence.generation.transactions import generate_transaction_tables
from baltic_sales_intelligence.generation.validation import validate_reference_data

_CUSTOMER_ADJECTIVES = (
    "Amber",
    "Aster",
    "Baltic",
    "Bright",
    "Cedar",
    "Clear",
    "Cobalt",
    "Delta",
    "Ever",
    "Granite",
    "Harbor",
    "Ironwood",
    "Juniper",
    "Keystone",
    "Linden",
    "Meridian",
    "Northstar",
    "Oakline",
    "Pioneer",
    "Quartz",
    "Rivermark",
    "Summit",
    "Timber",
    "Union",
    "Vertex",
    "Westbridge",
)
_CUSTOMER_NOUNS = (
    "Assembly",
    "Commercial",
    "Components",
    "Contracting",
    "Engineering",
    "Fabrication",
    "Facilities",
    "Industrial",
    "Logistics",
    "Maintenance",
    "Materials",
    "Operations",
    "Processing",
    "Projects",
    "Services",
    "Solutions",
    "Supply",
    "Systems",
    "Trading",
    "Works",
)
_PERSON_FIRST = (
    "Aira",
    "Aksel",
    "Alina",
    "Andris",
    "Dalia",
    "Edgars",
    "Egle",
    "Elina",
    "Emil",
    "Greta",
    "Ilze",
    "Janis",
    "Kadri",
    "Karlis",
    "Krista",
    "Laima",
    "Lukas",
    "Maarja",
    "Mantas",
    "Marta",
    "Nora",
    "Oskar",
    "Paulius",
    "Rasa",
    "Reinis",
    "Sander",
    "Tomas",
    "Viktor",
    "Vilma",
    "Zane",
)
_PERSON_LAST = (
    "Auden",
    "Berzin",
    "Dainis",
    "Elver",
    "Gailis",
    "Ilves",
    "Janson",
    "Kalnin",
    "Kask",
    "Laine",
    "Lember",
    "Mets",
    "Neris",
    "Ozols",
    "Paju",
    "Raud",
    "Saar",
    "Tamm",
    "Udris",
    "Vale",
)
_PRODUCT_TERMS = (
    "Core",
    "Edge",
    "Flex",
    "Guard",
    "Max",
    "Prime",
    "Pro",
    "Rapid",
    "Secure",
    "Select",
    "Standard",
    "Strong",
    "Ultra",
    "Utility",
    "Works",
)
_SUPPLIER_COUNTRIES = ("LT", "LV", "EE", "DE", "PL", "FI", "SE", "CZ")


@dataclass(frozen=True)
class GenerationResult:
    """Validated in-memory tables and their deterministic manifest."""

    tables: dict[str, pd.DataFrame]
    manifest: dict[str, Any]
    output_directory: Path | None = None


def _rng(
    configuration: ProjectConfiguration, component: str, substream: int = 0
) -> np.random.Generator:
    component_id = getattr(configuration.generation.component_ids, component)
    sequence = np.random.SeedSequence(
        configuration.generation.master_seed,
        spawn_key=(component_id, substream),
    )
    return np.random.Generator(np.random.PCG64(sequence))


def _random_date(rng: np.random.Generator, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=int(rng.integers(0, span + 1)))


def _choice_excluding(rng: np.random.Generator, values: tuple[str, ...], old: str) -> str:
    candidates = tuple(value for value in values if value != old)
    return str(rng.choice(candidates))


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def _reference_tables(configuration: ProjectConfiguration) -> dict[str, pd.DataFrame]:
    catalogue = configuration.generation.catalogue
    categories = [(f"CAT-{index:02d}", name) for index, name in enumerate(catalogue.categories, 1)]
    subcategories: list[tuple[str, str, str]] = []
    for category_index, (category_id, category_name) in enumerate(categories, 1):
        for sub_index, subcategory_name in enumerate(catalogue.subcategories[category_name], 1):
            subcategories.append(
                (f"SUB-{category_index:02d}-{sub_index:02d}", category_id, subcategory_name)
            )
    channels = tuple((code, name) for code, name in CHANNELS)
    if tuple(catalogue.channels) != tuple(name for _, name in channels):
        raise ValueError("configured channels do not match the frozen SPEC-03 catalogue")
    if tuple(catalogue.categories) != tuple(name for _, name in categories):
        raise ValueError("configured categories do not match the frozen SPEC-03 catalogue")
    return {
        "countries": pd.DataFrame(COUNTRIES, columns=PUBLIC_COLUMNS["countries"]),
        "regions": pd.DataFrame(REGIONS, columns=PUBLIC_COLUMNS["regions"]),
        "cities": pd.DataFrame(CITIES, columns=PUBLIC_COLUMNS["cities"]),
        "customer_segments": pd.DataFrame(SEGMENTS, columns=PUBLIC_COLUMNS["customer_segments"]),
        "industries": pd.DataFrame(INDUSTRIES, columns=PUBLIC_COLUMNS["industries"]),
        "product_categories": pd.DataFrame(
            categories, columns=PUBLIC_COLUMNS["product_categories"]
        ),
        "product_subcategories": pd.DataFrame(
            subcategories, columns=PUBLIC_COLUMNS["product_subcategories"]
        ),
        "sales_channels": pd.DataFrame(channels, columns=PUBLIC_COLUMNS["sales_channels"]),
    }


def _generate_suppliers(configuration: ProjectConfiguration) -> pd.DataFrame:
    rng = _rng(configuration, "products", 1)
    rows = []
    for number in range(1, configuration.generation.population.suppliers + 1):
        adjective = _CUSTOMER_ADJECTIVES[int(rng.integers(len(_CUSTOMER_ADJECTIVES)))]
        noun = _CUSTOMER_NOUNS[int(rng.integers(len(_CUSTOMER_NOUNS)))]
        rows.append(
            (
                f"SUP-{number:03d}",
                f"{adjective} {noun} Supply {number:03d}",
                str(rng.choice(_SUPPLIER_COUNTRIES)),
            )
        )
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["suppliers"])


def _generate_salespeople(configuration: ProjectConfiguration) -> pd.DataFrame:
    rng = _rng(configuration, "salespeople")
    population = configuration.generation.population.salespeople
    weights = np.array(configuration.generation.distributions.country_weights)
    country_counts = np.floor(weights * population).astype(int)
    for index in np.argsort(-(weights * population - country_counts))[
        : population - country_counts.sum()
    ]:
        country_counts[index] += 1
    countries = [
        code
        for code, count in zip(("LT", "LV", "EE"), country_counts, strict=True)
        for _ in range(int(count))
    ]
    rng.shuffle(countries)
    region_by_country = {
        code: [region_id for region_id, country, *_ in REGIONS if country == code]
        for code in ("LT", "LV", "EE")
    }
    rows = []
    separation_count = max(1, round(population * 0.10)) if population >= 6 else 0
    separated = set(rng.choice(population, size=separation_count, replace=False).tolist())
    for index, country in enumerate(countries):
        number = index + 1
        first = _PERSON_FIRST[int(rng.integers(len(_PERSON_FIRST)))]
        last = _PERSON_LAST[int(rng.integers(len(_PERSON_LAST)))]
        hire = _random_date(rng, date(2017, 1, 1), date(2024, 12, 31))
        termination = None
        if index in separated:
            lower = max(hire + timedelta(days=180), date(2023, 1, 1))
            if lower <= date(2026, 6, 30):
                termination = _random_date(rng, lower, date(2026, 6, 30))
        rows.append(
            (
                f"SP-{number:03d}",
                f"{first} {last} {number:02d}",
                country,
                str(rng.choice(region_by_country[country])),
                hire,
                termination,
                None,
            )
        )
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["salespeople"])


def _customer_name(rng: np.random.Generator, number: int) -> str:
    adjective = _CUSTOMER_ADJECTIVES[int(rng.integers(len(_CUSTOMER_ADJECTIVES)))]
    noun = _CUSTOMER_NOUNS[int(rng.integers(len(_CUSTOMER_NOUNS)))]
    return f"{adjective} {noun} Group {number:04d}"


def _generate_customers_and_history(
    configuration: ProjectConfiguration,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    customer_rng = _rng(configuration, "customers")
    history_rng = _rng(configuration, "customer_history")
    count = configuration.generation.population.customers
    distribution = configuration.generation.distributions
    scenario_controls = {control.story_id: control for control in load_scenario_controls()}
    reporting = configuration.application.reporting
    country_codes = ("LT", "LV", "EE")
    segment_codes = tuple(code for code, _ in SEGMENTS)
    industry_codes = tuple(code for code, _ in INDUSTRIES)
    channel_codes = tuple(code for code, _ in CHANNELS)
    city_rows = {code: [row for row in CITIES if row[2] == code] for code in country_codes}
    preferred_channel_weights = {
        "SMALL_BUSINESS": (0.18, 0.20, 0.24, 0.25, 0.13),
        "MID_MARKET": (0.32, 0.25, 0.16, 0.22, 0.05),
        "ENTERPRISE": (0.55, 0.24, 0.07, 0.13, 0.01),
        "PUBLIC_SECTOR": (0.42, 0.28, 0.04, 0.25, 0.01),
        "TRADE_RESELLER": (0.30, 0.17, 0.14, 0.18, 0.21),
    }
    size_multipliers = {
        "SMALL_BUSINESS": (3.2, 1.5, 0.3, 0.08),
        "MID_MARKET": (0.35, 1.2, 2.0, 0.9),
        "ENTERPRISE": (0.05, 0.25, 1.4, 5.0),
        "PUBLIC_SECTOR": (0.15, 0.55, 1.8, 3.0),
        "TRADE_RESELLER": (0.8, 1.4, 1.2, 0.55),
    }
    industry_multipliers = {
        "ENTERPRISE": {0: 1.8, 2: 1.5, 8: 1.4},
        "PUBLIC_SECTOR": {10: 8.0, 9: 1.8},
        "TRADE_RESELLER": {7: 3.0, 2: 1.4},
    }
    acquisition_years = np.arange(2017, 2027)
    acquisition_weights = np.array((0.08, 0.08, 0.09, 0.10, 0.10, 0.11, 0.11, 0.11, 0.11, 0.11))

    customer_rows: list[tuple[Any, ...]] = []
    initial_states: list[dict[str, Any]] = []
    for number in range(1, count + 1):
        customer_id = f"CUST-{number:05d}"
        country = str(customer_rng.choice(country_codes, p=distribution.country_weights))
        possible_cities = city_rows[country]
        city_probabilities = tuple(distribution.city_weights[row[0]] for row in possible_cities)
        city = possible_cities[int(customer_rng.choice(len(possible_cities), p=city_probabilities))]
        segment = str(customer_rng.choice(segment_codes, p=distribution.segment_weights))
        size_probabilities = np.array(distribution.company_size_weights) * np.array(
            size_multipliers[segment]
        )
        size_probabilities /= size_probabilities.sum()
        size = str(customer_rng.choice(COMPANY_SIZE_BANDS, p=size_probabilities))
        industry_probabilities = np.array(distribution.industry_weights)
        for industry_index, multiplier in industry_multipliers.get(segment, {}).items():
            industry_probabilities[industry_index] *= multiplier
        industry_probabilities /= industry_probabilities.sum()
        industry = str(customer_rng.choice(industry_codes, p=industry_probabilities))
        year_weights = acquisition_weights.copy()
        if country in {"LV", "EE"}:
            year_weights *= np.linspace(0.82, 1.18, len(year_weights))
        # S07 changes the genuine LV acquisition hazard; no scenario field is
        # written to the resulting customer master.
        if country == "LV":
            acquisition_control = scenario_controls["S07"]
            year_weights[acquisition_years >= acquisition_control.start_date.year] *= float(
                acquisition_control.strength["acquisition_weight_multiplier"]
            )
        year_weights /= year_weights.sum()
        acquisition_year = int(customer_rng.choice(acquisition_years, p=year_weights))
        acquisition = _random_date(
            customer_rng,
            date(acquisition_year, 1, 1),
            reporting.period_end
            if acquisition_year == reporting.period_end.year
            else date(acquisition_year, 12, 31),
        )
        status = str(customer_rng.choice(ACCOUNT_STATUSES, p=distribution.account_status_weights))
        credit_tier = (
            None if customer_rng.random() < 0.08 else str(customer_rng.choice(CREDIT_TIERS))
        )
        preferred = str(customer_rng.choice(channel_codes, p=preferred_channel_weights[segment]))
        customer_rows.append(
            (
                customer_id,
                _customer_name(customer_rng, number),
                acquisition,
                status,
                industry,
                credit_tier,
                preferred,
            )
        )
        initial_states.append(
            {
                "customer_id": customer_id,
                "acquisition": acquisition,
                "segment": segment,
                "size": size,
                "city": city,
            }
        )

    eligible = [
        state
        for state in initial_states
        if state["acquisition"] <= reporting.period_end - timedelta(days=120)
    ]
    change_count = min(len(eligible), round(count * distribution.history_change_share))
    changed_ids = set()
    if change_count:
        selected = history_rng.choice(len(eligible), size=change_count, replace=False)
        changed_ids = {eligible[int(index)]["customer_id"] for index in selected}

    history_rows: list[tuple[Any, ...]] = []
    for state in initial_states:
        customer_id = state["customer_id"]
        versions: list[dict[str, Any]] = [state.copy()]
        if customer_id in changed_ids:
            change_start = _random_date(
                history_rng,
                state["acquisition"] + timedelta(days=90),
                reporting.period_end,
            )
            changed = state.copy()
            changed["valid_from"] = change_start
            change_type = int(history_rng.integers(3))
            if change_type == 0:
                changed["segment"] = _choice_excluding(history_rng, segment_codes, state["segment"])
            elif change_type == 1:
                changed["size"] = _choice_excluding(history_rng, COMPANY_SIZE_BANDS, state["size"])
            else:
                current_city = state["city"]
                other_cities = tuple(row for row in CITIES if row[0] != current_city[0])
                changed["city"] = other_cities[int(history_rng.integers(len(other_cities)))]
            versions.append(changed)
        for version_index, version in enumerate(versions, 1):
            start = state["acquisition"] if version_index == 1 else version["valid_from"]
            end = (
                versions[version_index].get("valid_from") if version_index < len(versions) else None
            )
            city = version["city"]
            history_rows.append(
                (
                    f"CVER-{int(customer_id[-5:]):05d}-{version_index:02d}",
                    customer_id,
                    start,
                    end,
                    version["segment"],
                    version["size"],
                    city[0],
                    city[1],
                    city[2],
                    end is None,
                )
            )
    customers = pd.DataFrame(customer_rows, columns=PUBLIC_COLUMNS["customers"])
    history = pd.DataFrame(history_rows, columns=PUBLIC_COLUMNS["customer_attribute_history"])
    return customers, history


def _generate_assignments(
    configuration: ProjectConfiguration,
    customers: pd.DataFrame,
    history: pd.DataFrame,
    salespeople: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(configuration, "assignments")
    distribution = configuration.generation.distributions
    extension_end = configuration.application.reporting.extension_end
    initial_geo = history.sort_values("valid_from").groupby("customer_id", sort=False).first()
    people_by_country = {
        country: list(
            salespeople[salespeople["primary_country_code"] == country].itertuples(index=False)
        )
        for country in ("LT", "LV", "EE")
    }
    rows: list[tuple[Any, ...]] = []
    assignment_number = 0
    for customer in customers.itertuples(index=False):
        if rng.random() < distribution.assignment_gap_share:
            continue
        country = initial_geo.loc[customer.customer_id, "home_country_code"]
        eligible = [
            person
            for person in people_by_country[country]
            if person.hire_date <= extension_end
            and (
                person.termination_date is None
                or person.termination_date > customer.acquisition_date
            )
        ]
        if not eligible:
            continue
        owner = eligible[int(rng.integers(len(eligible)))]
        start = max(customer.acquisition_date, owner.hire_date)
        end = owner.termination_date
        transfer = rng.random() < distribution.ownership_transfer_share or end is not None
        transfer_date = None
        if transfer:
            lower = start + timedelta(days=30)
            upper = min(end or extension_end, extension_end)
            if lower < upper:
                transfer_date = end if end is not None else _random_date(rng, lower, upper)
        assignment_number += 1
        rows.append(
            (
                f"ASG-{assignment_number:06d}",
                customer.customer_id,
                owner.salesperson_id,
                start,
                transfer_date or end,
            )
        )
        if transfer_date is not None and transfer_date <= extension_end:
            replacements = [
                person
                for person in people_by_country[country]
                if person.salesperson_id != owner.salesperson_id
                and person.hire_date <= transfer_date
                and (person.termination_date is None or person.termination_date > extension_end)
            ]
            if replacements:
                replacement = replacements[int(rng.integers(len(replacements)))]
                assignment_number += 1
                rows.append(
                    (
                        f"ASG-{assignment_number:06d}",
                        customer.customer_id,
                        replacement.salesperson_id,
                        transfer_date,
                        None,
                    )
                )
            elif end is None:
                rows[-1] = (*rows[-1][:-1], None)
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["customer_account_assignments"])


def _allocate_counts(total: int, weights: tuple[float, ...]) -> np.ndarray:
    raw = np.array(weights) * total
    counts = np.floor(raw).astype(int)
    remainder = total - int(counts.sum())
    if remainder:
        for index in np.argsort(-(raw - counts))[:remainder]:
            counts[index] += 1
    return counts


def _generate_products(
    configuration: ProjectConfiguration,
    subcategories: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(configuration, "products", 2)
    count = configuration.generation.population.products
    category_counts = _allocate_counts(count, configuration.generation.catalogue.category_weights)
    category_ids = tuple(f"CAT-{number:02d}" for number in range(1, 13))
    category_assignment = [
        category
        for category, amount in zip(category_ids, category_counts, strict=True)
        for _ in range(int(amount))
    ]
    rng.shuffle(category_assignment)
    subcategory_by_category = {
        category: tuple(subcategories[subcategories["category_id"] == category]["subcategory_id"])
        for category in category_ids
    }
    price_bands = (
        (8, 180),
        (5, 140),
        (3, 120),
        (4, 250),
        (1, 80),
        (3, 110),
        (12, 220),
        (2, 90),
        (8, 450),
        (30, 900),
        (2, 130),
        (10, 500),
    )
    rows = []
    reporting = configuration.application.reporting
    discontinued_count = round(count * 0.08)
    discontinued_ids = set(rng.choice(count, size=discontinued_count, replace=False).tolist())
    for index, category_id in enumerate(category_assignment):
        number = index + 1
        category_index = int(category_id[-2:]) - 1
        low, high = price_bands[category_index]
        sampled = low + float(rng.beta(1.8, 4.0)) * (high - low)
        list_price = _money(sampled)
        cost_ratio = Decimal(str(0.48 + float(rng.beta(3, 4)) * 0.30))
        cost = (list_price * cost_ratio).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
        launch = _random_date(rng, date(2018, 1, 1), reporting.period_end)
        discontinued = None
        if index in discontinued_ids and launch <= reporting.period_end - timedelta(days=180):
            discontinued = _random_date(rng, launch + timedelta(days=180), reporting.period_end)
        subcategory = str(rng.choice(subcategory_by_category[category_id]))
        term = _PRODUCT_TERMS[int(rng.integers(len(_PRODUCT_TERMS)))]
        rows.append(
            (
                f"PROD-{number:04d}",
                f"BTS-{number:06d}",
                f"{term} {subcategory} {number:04d}",
                subcategory,
                launch,
                discontinued,
                "discontinued" if discontinued else "active",
                list_price,
                cost,
            )
        )
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["products"])


def _generate_supplier_relationships(
    configuration: ProjectConfiguration,
    products: pd.DataFrame,
    suppliers: pd.DataFrame,
) -> pd.DataFrame:
    rng = _rng(configuration, "products", 3)
    supplier_ids = tuple(suppliers["supplier_id"])
    relationship_rows: list[tuple[Any, ...]] = []
    relationship_number = 0
    alternative_count = round(
        len(products) * configuration.generation.catalogue.alternative_supplier_share
    )
    alternative_products = set(
        rng.choice(len(products), size=alternative_count, replace=False).tolist()
    )
    switch_count = round(len(products) * 0.10)
    switch_products = set(rng.choice(len(products), size=switch_count, replace=False).tolist())
    for index, product in enumerate(products.itertuples(index=False)):
        primary = str(rng.choice(supplier_ids))
        relationship_end = product.discontinued_date
        switched = False
        if index in switch_products:
            upper = relationship_end or configuration.application.reporting.extension_end
            lower = product.launch_date + timedelta(days=180)
            if lower < upper:
                switch_date = _random_date(rng, lower, upper - timedelta(days=1))
                relationship_number += 1
                relationship_rows.append(
                    (
                        f"PSR-{relationship_number:06d}",
                        product.product_id,
                        primary,
                        "primary",
                        product.launch_date,
                        switch_date,
                    )
                )
                second = _choice_excluding(rng, supplier_ids, primary)
                relationship_number += 1
                relationship_rows.append(
                    (
                        f"PSR-{relationship_number:06d}",
                        product.product_id,
                        second,
                        "primary",
                        switch_date,
                        relationship_end,
                    )
                )
                primary = second
                switched = True
        if not switched:
            relationship_number += 1
            relationship_rows.append(
                (
                    f"PSR-{relationship_number:06d}",
                    product.product_id,
                    primary,
                    "primary",
                    product.launch_date,
                    relationship_end,
                )
            )
        if index in alternative_products:
            used = {row[2] for row in relationship_rows if row[1] == product.product_id}
            candidates = tuple(value for value in supplier_ids if value not in used)
            alternative = str(rng.choice(candidates))
            relationship_number += 1
            relationship_rows.append(
                (
                    f"PSR-{relationship_number:06d}",
                    product.product_id,
                    alternative,
                    "alternative",
                    product.launch_date,
                    relationship_end,
                )
            )
    return pd.DataFrame(relationship_rows, columns=PUBLIC_COLUMNS["product_supplier_relationships"])


def _generate_date_dimension(configuration: ProjectConfiguration) -> pd.DataFrame:
    reporting = configuration.application.reporting
    rows = []
    current = reporting.period_start
    while current <= reporting.extension_end:
        iso = current.isocalendar()
        rows.append(
            (
                int(current.strftime("%Y%m%d")),
                current,
                current.year,
                (current.month - 1) // 3 + 1,
                current.month,
                current.replace(day=1),
                iso.week,
                iso.year,
                iso.weekday,
                current.day,
                current.timetuple().tm_yday,
                iso.weekday >= 6,
                current <= reporting.period_end,
            )
        )
        current += timedelta(days=1)
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["date_dimension"])


def generate_reference_tables(configuration: ProjectConfiguration) -> dict[str, pd.DataFrame]:
    """Generate all canonical public truth tables without writing to disk."""

    tables = _reference_tables(configuration)
    tables["suppliers"] = _generate_suppliers(configuration)
    tables["salespeople"] = _generate_salespeople(configuration)
    customers, history = _generate_customers_and_history(configuration)
    tables["customers"] = customers
    tables["customer_attribute_history"] = history
    tables["customer_account_assignments"] = _generate_assignments(
        configuration, customers, history, tables["salespeople"]
    )
    tables["products"] = _generate_products(configuration, tables["product_subcategories"])
    tables["product_supplier_relationships"] = _generate_supplier_relationships(
        configuration, tables["products"], tables["suppliers"]
    )
    tables["date_dimension"] = _generate_date_dimension(configuration)
    tables.update(generate_transaction_tables(configuration, tables))
    ordered = {name: tables[name].reset_index(drop=True) for name in PUBLIC_COLUMNS}
    validate_reference_data(ordered, configuration)
    return ordered


def _canonical_value(value: Any, column: str | None = None) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if column in {"discount_rate", "vat_rate_fraction"}:
            return f"{value:.6f}"
        if column and (
            column.startswith("posted_")
            or column in {"outbound_freight_eur", "returns_freight_eur", "target_value"}
        ):
            return f"{value:.2f}"
        return f"{value:.4f}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def canonical_content_hash(frame: pd.DataFrame) -> str:
    """Hash logical typed content independently of container metadata."""

    digest = hashlib.sha256()
    digest.update(("\x1f".join(frame.columns) + "\n").encode())
    for row in frame.itertuples(index=False, name=None):
        digest.update(
            (
                "\x1f".join(
                    _canonical_value(value, column)
                    for column, value in zip(frame.columns, row, strict=True)
                )
                + "\n"
            ).encode()
        )
    return digest.hexdigest()


def _package_versions() -> dict[str, str]:
    names = ("numpy", "pandas", "pyarrow", "pydantic", "PyYAML")
    return {name: importlib.metadata.version(name) for name in names}


def build_manifest(
    configuration: ProjectConfiguration, tables: dict[str, pd.DataFrame]
) -> dict[str, Any]:
    """Build the deterministic portion of the generation manifest."""

    effective_config_hash = hashlib.sha256(
        f"{configuration.deterministic_hash()}:{controls_hash()}".encode()
    ).hexdigest()
    return {
        "schema_id": "bsi.synthetic_canonical_truth",
        "schema_version": 1,
        "stage": "generate",
        "generator_version": __version__,
        "master_seed": configuration.generation.master_seed,
        "component_ids": configuration.generation.component_ids.model_dump(),
        # The private controls influence generation, so their content hash is
        # folded in without publishing their path, labels, or parameters.
        "config_hash": effective_config_hash,
        "quality_status": "passed",
        "stage_duration_seconds": 0.0,
        "source_paths": [
            "config/application.yaml",
            "config/generation.yaml",
            "config/data_quality.yaml",
            "config/analytics.yaml",
        ],
        "package_versions": _package_versions(),
        "datasets": {
            name: {
                "schema_id": f"bsi.truth.{name}",
                "schema_version": 1,
                "row_count": len(frame),
                "column_count": len(frame.columns),
                "columns": list(frame.columns),
                "canonical_sha256": canonical_content_hash(frame),
                "csv_path": f"{name}.csv",
                "parquet_path": f"{name}.parquet",
            }
            for name, frame in tables.items()
        },
    }


def build_distribution_summary(
    configuration: ProjectConfiguration, tables: dict[str, pd.DataFrame]
) -> pd.DataFrame:
    """Compare generated population shares with configured simulation assumptions."""

    rows: list[tuple[Any, ...]] = []
    initial = (
        tables["customer_attribute_history"]
        .sort_values("valid_from")
        .groupby("customer_id", sort=False)
        .first()
    )
    customer_count = len(initial)
    distribution = configuration.generation.distributions

    def add_group(
        name: str,
        values: tuple[str, ...],
        expected: tuple[float, ...],
        actual: pd.Series,
        base_tolerance: float = 0.03,
    ) -> None:
        for value, expected_fraction in zip(values, expected, strict=True):
            actual_fraction = float(actual.get(value, 0)) / customer_count
            sampling_tolerance = (
                3 * (expected_fraction * (1 - expected_fraction) / customer_count) ** 0.5
            )
            tolerance = max(base_tolerance, sampling_tolerance)
            rows.append(
                (
                    name,
                    value,
                    expected_fraction,
                    actual_fraction,
                    tolerance,
                    abs(actual_fraction - expected_fraction) <= tolerance,
                )
            )

    add_group(
        "customer_country",
        ("LT", "LV", "EE"),
        distribution.country_weights,
        initial["home_country_code"].value_counts(),
    )
    add_group(
        "customer_segment",
        tuple(code for code, _ in SEGMENTS),
        distribution.segment_weights,
        initial["segment_code"].value_counts(),
    )
    relationships = tables["product_supplier_relationships"]
    alternative_actual = relationships.loc[
        relationships["relationship_type"] == "alternative", "product_id"
    ].nunique() / len(tables["products"])
    alternative_expected = configuration.generation.catalogue.alternative_supplier_share
    alternative_tolerance = configuration.generation.catalogue.alternative_supplier_tolerance
    rows.append(
        (
            "alternative_supplier",
            "approved_product_share",
            alternative_expected,
            alternative_actual,
            alternative_tolerance,
            abs(alternative_actual - alternative_expected) <= alternative_tolerance,
        )
    )
    return pd.DataFrame(
        rows,
        columns=(
            "distribution",
            "value",
            "expected_fraction",
            "actual_fraction",
            "tolerance",
            "passed",
        ),
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    serialised = pd.DataFrame(
        {
            column: frame[column].map(lambda value, name=column: _canonical_value(value, name))
            for column in frame.columns
        }
    )
    serialised.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    date_columns = {
        column
        for column in frame.columns
        if column.endswith("_date") or column in {"valid_from", "valid_to", "full_date"}
    }
    unit_decimal_columns = {
        "reference_list_unit_price_eur",
        "reference_standard_unit_cost_eur",
        "list_unit_price_eur",
        "realised_unit_price_eur",
        "unit_cost_eur",
        "refund_unit_ex_vat_eur",
    }
    rate_decimal_columns = {"discount_rate", "vat_rate_fraction"}
    money_decimal_columns = {
        column
        for column in frame.columns
        if column.startswith("posted_")
        or column in {"outbound_freight_eur", "returns_freight_eur", "target_value"}
    }
    arrays = []
    for column in frame.columns:
        arrow_type = None
        if column in date_columns:
            arrow_type = pa.date32()
        elif column in unit_decimal_columns:
            arrow_type = pa.decimal128(18, 4)
        elif column in rate_decimal_columns:
            arrow_type = pa.decimal128(9, 6)
        elif column in money_decimal_columns:
            arrow_type = pa.decimal128(18, 2)
        values = [None if pd.isna(value) else value for value in frame[column].tolist()]
        arrays.append(pa.array(values, type=arrow_type))
    table = pa.Table.from_arrays(arrays, names=list(frame.columns))
    pq.write_table(
        table,
        path,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_reference_data(
    configuration: ProjectConfiguration,
    output_directory: str | Path | None = None,
    *,
    publish: bool = True,
) -> GenerationResult:
    """Generate, validate, and optionally atomically publish canonical truth data."""

    tables = generate_reference_tables(configuration)
    manifest = build_manifest(configuration, tables)
    distribution_summary = build_distribution_summary(configuration, tables)
    if not bool(distribution_summary["passed"].all()):
        raise ValueError("generated distribution summary failed configured tolerances")
    if not publish:
        manifest["quality_artifacts"] = {
            "distribution_summary": {
                "row_count": len(distribution_summary),
                "status": "passed",
            }
        }
        return GenerationResult(tables=tables, manifest=manifest)
    if output_directory is None:
        raise ValueError("output_directory is required when publish=True")
    target = Path(output_directory)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".generation-", dir=target) as temporary:
        staging = Path(temporary)
        for name, frame in tables.items():
            csv_path = staging / f"{name}.csv"
            parquet_path = staging / f"{name}.parquet"
            _write_csv(frame, csv_path)
            _write_parquet(frame, parquet_path)
            manifest["datasets"][name]["csv_sha256"] = _file_hash(csv_path)
            manifest["datasets"][name]["parquet_sha256"] = _file_hash(parquet_path)
        summary_path = staging / "distribution_summary.csv"
        _write_csv(distribution_summary, summary_path)
        manifest["quality_artifacts"] = {
            "distribution_summary": {
                "path": summary_path.name,
                "row_count": len(distribution_summary),
                "sha256": _file_hash(summary_path),
                "status": "passed",
            }
        }
        manifest_path = staging / "generation_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for staged_path in sorted(staging.iterdir()):
            os.replace(staged_path, target / staged_path.name)
    return GenerationResult(tables=tables, manifest=manifest, output_directory=target)
