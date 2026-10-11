"""Focused contracts for deterministic raw-data defect injection."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation import generate_reference_data
from baltic_sales_intelligence.generation.generator import canonical_content_hash
from baltic_sales_intelligence.quality import (
    inject_defects,
    load_validated_truth,
    publish_raw_injection,
)
from baltic_sales_intelligence.quality.injection import REGISTRY_COLUMNS


@pytest.fixture(scope="module")
def injected(small_configuration: ProjectConfiguration, small_tables):
    return inject_defects(small_configuration, small_tables)


def _registry_row(result, defect_id: str, variant: str) -> pd.Series:
    rows = result.registry[
        (result.registry["defect_id"] == defect_id)
        & (result.registry["variant"] == variant)
    ]
    assert len(rows) >= 1
    return rows.iloc[0]


def _raw_row(result, registry_row: pd.Series) -> pd.Series:
    return result.tables[registry_row["dataset"]].iloc[
        int(registry_row["raw_row_number"]) - 2
    ]


def test_all_defects_and_compound_variants_change_raw_content(injected) -> None:
    expected_variants = {
        "DQ01": {"exact_record"},
        "DQ02": {"conflicting_business_key"},
        "DQ03": {"country_alias"},
        "DQ04": {"case_whitespace"},
        "DQ05": {"missing_region"},
        "DQ06": {"malformed_date"},
        "DQ07": {"zero", "negative"},
        "DQ08": {"above_one", "negative"},
        "DQ09": {"missing_nonhistorical_category", "missing_historical_cost"},
        "DQ10": {"orphan_customer", "orphan_product", "orphan_salesperson"},
        "DQ11": {"malformed_text_amount"},
        "DQ12": {"nonpositive_accepted_quantity", "cumulative_cap_violation"},
        "DQ13": {
            "overlapping_customer_scd",
            "overlapping_account_ownership",
            "overlapping_supplier_interval",
        },
        "DQ14": {"exact_target_duplicate", "conflicting_target_duplicate"},
    }
    actual = injected.registry.groupby("defect_id")["variant"].agg(set).to_dict()
    assert actual == expected_variants

    exact = _registry_row(injected, "DQ01", "exact_record")
    exact_row = _raw_row(injected, exact)
    source_row = injected.tables["order_lines"].iloc[int(exact["target_source_row"]) - 2]
    assert exact_row.equals(source_row)

    conflict = _registry_row(injected, "DQ02", "conflicting_business_key")
    conflict_row = _raw_row(injected, conflict)
    order_group = injected.tables["orders"][
        injected.tables["orders"]["order_id"] == conflict_row["order_id"]
    ]
    assert len(order_group) == 2
    assert order_group["payment_terms_code"].nunique() == 2

    assert _raw_row(injected, _registry_row(injected, "DQ03", "country_alias"))[
        "home_country_code"
    ] in {"Lithuania", "Latvija", "Estonia"}
    terms = _raw_row(injected, _registry_row(injected, "DQ04", "case_whitespace"))[
        "payment_terms_code"
    ]
    assert terms != terms.strip().lower()
    assert pd.isna(
        _raw_row(injected, _registry_row(injected, "DQ05", "missing_region"))[
            "home_region_id"
        ]
    )
    assert _raw_row(injected, _registry_row(injected, "DQ06", "malformed_date"))[
        "order_date"
    ] == "2025-13-40"
    assert set(injected.tables["order_lines"]["quantity_ordered"]) >= {-1, 0}
    assert {str(value) for value in injected.tables["order_lines"]["discount_rate"]} >= {
        "-0.050000",
        "1.250000",
    }
    assert injected.tables["product_subcategories"]["category_id"].isna().any()
    assert injected.tables["order_lines"]["unit_cost_eur"].isna().any()
    assert "ORPHAN-CUSTOMER" in set(injected.tables["orders"]["customer_id"])
    assert "ORPHAN-SALESPERSON" in set(
        injected.tables["orders"]["credited_salesperson_id"]
    )
    salesperson_orphan = _registry_row(injected, "DQ10", "orphan_salesperson")
    assert json.loads(salesperson_orphan["original_value"]) is not None
    assert "ORPHAN-PRODUCT" in set(injected.tables["order_lines"]["product_id"])
    assert "EUR ??" in set(injected.tables["order_lines"]["posted_net_sales_eur"])


def test_clean_truth_has_none_of_the_injected_defects(small_tables) -> None:
    assert not small_tables["order_lines"].duplicated().any()  # DQ01
    assert small_tables["orders"]["order_id"].is_unique  # DQ02
    assert set(small_tables["customer_attribute_history"]["home_country_code"]) <= {
        "LT", "LV", "EE"
    }  # DQ03
    assert all(
        value == value.strip().lower()
        for value in small_tables["orders"]["payment_terms_code"]
    )  # DQ04
    assert small_tables["customer_attribute_history"]["home_region_id"].notna().all()  # DQ05
    assert all(not isinstance(value, str) for value in small_tables["orders"]["order_date"])  # DQ06
    assert (small_tables["order_lines"]["quantity_ordered"] > 0).all()  # DQ07
    assert all(0 <= value <= 1 for value in small_tables["order_lines"]["discount_rate"])  # DQ08
    assert small_tables["product_subcategories"]["category_id"].notna().all()
    assert small_tables["order_lines"]["unit_cost_eur"].notna().all()  # DQ09
    assert set(small_tables["orders"]["customer_id"]) <= set(
        small_tables["customers"]["customer_id"]
    )
    assert set(small_tables["orders"]["credited_salesperson_id"].dropna()) <= set(
        small_tables["salespeople"]["salesperson_id"]
    )
    assert set(small_tables["order_lines"]["product_id"]) <= set(
        small_tables["products"]["product_id"]
    )  # DQ10
    assert all(
        not isinstance(value, str)
        for value in small_tables["order_lines"]["posted_net_sales_eur"]
    )  # DQ11
    accepted = small_tables["returns"].query("return_status == 'accepted'")
    assert (accepted["accepted_quantity"] > 0).all()  # DQ12
    assert not small_tables["customer_attribute_history"].duplicated(
        ["customer_id", "valid_from", "valid_to"]
    ).any()
    assert not small_tables["customer_account_assignments"].duplicated(
        ["customer_id", "valid_from", "valid_to"]
    ).any()
    assert not small_tables["product_supplier_relationships"].duplicated(
        ["product_id", "supplier_id", "valid_from", "valid_to"]
    ).any()  # DQ13
    assert small_tables["sales_targets"]["target_id"].is_unique  # DQ14


def test_return_cap_interval_and_target_defects_preserve_conflicting_records(
    injected, small_tables
) -> None:
    invalid = _raw_row(
        injected, _registry_row(injected, "DQ12", "nonpositive_accepted_quantity")
    )
    assert invalid["return_status"] == "accepted"
    assert invalid["accepted_quantity"] == 0

    cap = _raw_row(injected, _registry_row(injected, "DQ12", "cumulative_cap_violation"))
    accepted = injected.tables["returns"].query(
        "order_line_id == @cap.order_line_id and return_status == 'accepted'"
    ).sort_values(["return_decision_date", "return_id"])
    sold = small_tables["order_lines"].set_index("order_line_id").loc[
        cap["order_line_id"], "quantity_ordered"
    ]
    assert accepted["accepted_quantity"].sum() > sold
    assert accepted.iloc[-1]["return_id"] == cap["return_id"]

    interval_variants = {
        "overlapping_customer_scd": ("customer_attribute_history", "customer_id"),
        "overlapping_account_ownership": ("customer_account_assignments", "customer_id"),
        "overlapping_supplier_interval": (
            "product_supplier_relationships",
            "product_id",
        ),
    }
    for variant, (dataset, group_key) in interval_variants.items():
        row = _raw_row(injected, _registry_row(injected, "DQ13", variant))
        matches = injected.tables[dataset][
            (injected.tables[dataset][group_key] == row[group_key])
            & (injected.tables[dataset]["valid_from"] == row["valid_from"])
            & (injected.tables[dataset]["valid_to"].fillna("open") == (
                "open" if pd.isna(row["valid_to"]) else row["valid_to"]
            ))
        ]
        assert len(matches) >= 2

    exact = _raw_row(injected, _registry_row(injected, "DQ14", "exact_target_duplicate"))
    exact_group = injected.tables["sales_targets"][
        injected.tables["sales_targets"]["target_id"] == exact["target_id"]
    ]
    assert len(exact_group) == 2
    assert exact_group.nunique(dropna=False).max() == 1
    conflicting = _raw_row(
        injected, _registry_row(injected, "DQ14", "conflicting_target_duplicate")
    )
    conflict_group = injected.tables["sales_targets"][
        injected.tables["sales_targets"]["target_id"] == conflicting["target_id"]
    ]
    assert len(conflict_group) == 2
    assert conflict_group["target_value"].nunique() == 2


def test_region_defect_is_limited_to_uniquely_inferable_city_mapping(
    injected, small_configuration: ProjectConfiguration, small_tables
) -> None:
    region_defect = _registry_row(injected, "DQ05", "missing_region")
    row = _raw_row(injected, region_defect)
    city_matches = small_tables["cities"][
        small_tables["cities"]["city_id"] == row["home_city_id"]
    ]
    assert city_matches["region_id"].nunique() == 1
    assert json.loads(region_defect["original_value"]) == city_matches.iloc[0]["region_id"]

    ambiguous = {name: frame.copy(deep=True) for name, frame in small_tables.items()}
    extra_city = city_matches.iloc[0].copy()
    extra_city["region_id"] = next(
        region_id
        for region_id in ambiguous["regions"]["region_id"]
        if region_id != extra_city["region_id"]
    )
    ambiguous["cities"] = pd.concat(
        [ambiguous["cities"], extra_city.to_frame().T], ignore_index=True
    )
    reinjected = inject_defects(small_configuration, ambiguous)
    injected_city_ids = {
        _raw_row(reinjected, registry_row)["home_city_id"]
        for _, registry_row in reinjected.registry.query("defect_id == 'DQ05'").iterrows()
    }
    assert row["home_city_id"] not in injected_city_ids


def test_registry_provenance_and_truth_immutability(injected, small_tables) -> None:
    assert tuple(injected.registry.columns) == REGISTRY_COLUMNS
    assert not injected.registry.isna().any().any()
    assert set(injected.registry["defect_id"]) == {f"DQ{number:02d}" for number in range(1, 15)}
    assert injected.provenance.groupby("dataset").size().to_dict() == {
        name: len(frame) for name, frame in injected.tables.items()
    }
    assert not injected.provenance.duplicated(["dataset", "raw_row_number"]).any()
    duplicate_sources = injected.provenance.query("record_instance > 1")
    assert not duplicate_sources.empty
    assert set(REGISTRY_COLUMNS).isdisjoint(
        column for frame in injected.tables.values() for column in frame.columns
    )


def test_output_is_deterministic_and_rule_streams_are_independent(
    small_configuration: ProjectConfiguration, small_tables
) -> None:
    source_hashes = {
        name: canonical_content_hash(frame) for name, frame in small_tables.items()
    }
    first = inject_defects(small_configuration, small_tables)
    assert source_hashes == {
        name: canonical_content_hash(frame) for name, frame in small_tables.items()
    }
    second = inject_defects(small_configuration, small_tables)
    for name in first.tables:
        assert_frame_equal(first.tables[name], second.tables[name])
    assert_frame_equal(first.registry, second.registry)
    assert_frame_equal(first.provenance, second.provenance)

    payload = small_configuration.model_dump()
    payload["data_quality"]["defects"]["DQ01"].update(
        {"seed_component": 2201, "fraction": 0.015}
    )
    changed = inject_defects(ProjectConfiguration.model_validate(payload), small_tables)
    unaffected = first.registry.query("defect_id != 'DQ01'").reset_index(drop=True)
    changed_unaffected = changed.registry.query("defect_id != 'DQ01'").reset_index(drop=True)
    assert_frame_equal(unaffected, changed_unaffected)


def test_source_manifest_checksums_and_atomic_publication(
    tmp_path: Path,
    small_configuration: ProjectConfiguration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth = tmp_path / "truth"
    generate_reference_data(small_configuration, truth)
    loaded, manifest, source_hash = load_validated_truth(truth)
    assert set(loaded) == set(manifest["datasets"])
    assert len(source_hash) == 64

    raw = tmp_path / "raw"
    registry = tmp_path / "qa" / "injections"
    published = publish_raw_injection(small_configuration, truth, raw, registry)
    raw_manifest = json.loads((raw / "raw_injection_manifest.json").read_text())
    assert raw_manifest["batch_id"] == published.manifest["batch_id"]
    assert (registry / "private_injection_registry.csv").is_file()

    csv_path = truth / manifest["datasets"]["countries"]["csv_path"]
    csv_path.write_bytes(csv_path.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_validated_truth(truth)

    sentinel_raw = tmp_path / "failed-raw"
    sentinel_registry = tmp_path / "failed-qa"
    sentinel_raw.mkdir()
    sentinel_registry.mkdir()
    (sentinel_raw / "keep.txt").write_text("unchanged")
    (sentinel_registry / "keep.txt").write_text("unchanged")

    # Restore truth before exercising a staging failure.
    generate_reference_data(small_configuration, truth)
    import baltic_sales_intelligence.quality.injection as injection_module

    def fail_write(_frame: pd.DataFrame, _path: Path) -> None:
        raise OSError("simulated staging failure")

    monkeypatch.setattr(injection_module, "_write_csv", fail_write)
    with pytest.raises(OSError, match="simulated staging failure"):
        publish_raw_injection(
            small_configuration, truth, sentinel_raw, sentinel_registry
        )
    assert (sentinel_raw / "keep.txt").read_text() == "unchanged"
    assert (sentinel_registry / "keep.txt").read_text() == "unchanged"
