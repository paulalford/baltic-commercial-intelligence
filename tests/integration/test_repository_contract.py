"""Repository-level contracts for ignored generated content and local environments."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    "path",
    [
        ".venv/pyvenv.cfg",
        "data/truth/generated.csv",
        "data/raw/generated.csv",
        "data/quarantine/generated.parquet",
        "data/silver/generated.parquet",
        "data/database/warehouse.duckdb",
        "data/gold/generated.parquet",
        "data/qa/scenarios/private_truth_registry.csv",
    ],
)
def test_generated_content_and_local_environment_are_git_ignored(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "--quiet", path], cwd=ROOT, check=False)
    assert result.returncode == 0, f"expected Git to ignore {path}"


@pytest.mark.parametrize(
    "path",
    [
        "data/truth/.gitkeep",
        "data/raw/.gitkeep",
        "data/quarantine/.gitkeep",
        "data/silver/.gitkeep",
        "data/database/.gitkeep",
        "data/gold/.gitkeep",
    ],
)
def test_data_directory_markers_are_not_git_ignored(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "--quiet", path], cwd=ROOT, check=False)
    assert result.returncode == 1, f"expected Git to retain {path}"
