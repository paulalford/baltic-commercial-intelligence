"""Reproducibility, seed sensitivity, publication and manifest contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from baltic_sales_intelligence.generation import (
    canonical_content_hash,
    generate_reference_data,
    generate_reference_tables,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_same_seed_produces_identical_logical_content(small_configuration) -> None:
    first = generate_reference_tables(small_configuration)
    second = generate_reference_tables(small_configuration)
    assert {name: canonical_content_hash(table) for name, table in first.items()} == {
        name: canonical_content_hash(table) for name, table in second.items()
    }


def test_changed_seed_changes_stochastic_payload_but_not_taxonomies(
    small_configuration,
) -> None:
    first = generate_reference_tables(small_configuration)
    changed_generation = small_configuration.generation.model_copy(
        update={"master_seed": small_configuration.generation.master_seed + 1}
    )
    changed_config = small_configuration.model_copy(update={"generation": changed_generation})
    second = generate_reference_tables(changed_config)
    assert canonical_content_hash(first["customers"]) != canonical_content_hash(second["customers"])
    for name in (
        "countries",
        "regions",
        "cities",
        "customer_segments",
        "industries",
        "product_categories",
        "product_subcategories",
        "sales_channels",
    ):
        assert canonical_content_hash(first[name]) == canonical_content_hash(second[name])


def test_publication_is_byte_stable_and_manifest_hashes_match(
    tmp_path: Path,
    small_configuration,
) -> None:
    output = tmp_path / "truth"
    first = generate_reference_data(small_configuration, output)
    initial_hashes = {path.name: _sha256(path) for path in output.iterdir()}
    second = generate_reference_data(small_configuration, output)
    assert {path.name: _sha256(path) for path in output.iterdir()} == initial_hashes
    assert first.manifest == second.manifest
    assert first.manifest["quality_artifacts"]["distribution_summary"]["status"] == "passed"
    assert (output / "distribution_summary.csv").is_file()
    for name, metadata in first.manifest["datasets"].items():
        assert metadata["row_count"] == len(first.tables[name])
        assert metadata["csv_sha256"] == _sha256(output / metadata["csv_path"])
        assert metadata["parquet_sha256"] == _sha256(output / metadata["parquet_path"])
