"""Meaningful validation tests for the four-file configuration contract."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from baltic_sales_intelligence.config import ConfigurationError, load_configuration

CONFIG_DIRECTORY = Path(__file__).parents[2] / "config"


def _copy_configuration(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(CONFIG_DIRECTORY, target)
    return target


def test_loads_approved_configuration_bundle() -> None:
    configuration = load_configuration(CONFIG_DIRECTORY)

    assert configuration.application.project.markets == ("LT", "LV", "EE")
    assert configuration.generation.master_seed == 20261010
    assert configuration.analytics.forecasting.horizons == (1, 3, 6)


def test_hash_is_stable_for_semantically_identical_yaml(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    original_hash = load_configuration(copied).deterministic_hash()

    application_path = copied / "application.yaml"
    parsed = yaml.safe_load(application_path.read_text(encoding="utf-8"))
    application_path.write_text(
        yaml.safe_dump(parsed, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )

    assert load_configuration(copied).deterministic_hash() == original_hash


def test_hash_changes_when_an_explicit_non_contract_switch_changes(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    original_hash = load_configuration(copied).deterministic_hash()
    path = copied / "data_quality.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed["injection"]["enabled"] = False
    path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

    assert load_configuration(copied).deterministic_hash() != original_hash


def test_missing_required_file_has_helpful_error(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    (copied / "analytics.yaml").unlink()

    with pytest.raises(ConfigurationError, match=r"analytics\.yaml"):
        load_configuration(copied)


def test_malformed_yaml_identifies_source_file(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    (copied / "generation.yaml").write_text("population: [unterminated", encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"generation\.yaml"):
        load_configuration(copied)


def test_duplicate_yaml_key_is_rejected(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    path = copied / "application.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "schema_version: 1\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="duplicate key"):
        load_configuration(copied)


def test_required_business_field_cannot_be_omitted(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    path = copied / "application.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    del parsed["project"]["reporting_currency"]
    path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="reporting_currency"):
        load_configuration(copied)


def test_undeclared_key_is_rejected(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    path = copied / "analytics.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed["invented_threshold"] = 0.5
    path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invented_threshold"):
        load_configuration(copied)


def test_scalar_type_coercion_is_rejected(tmp_path: Path) -> None:
    copied = _copy_configuration(tmp_path)
    path = copied / "generation.yaml"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    parsed["population"]["customers"] = "4000"
    path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigurationError, match=r"population\.customers"):
        load_configuration(copied)
