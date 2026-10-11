"""CLI parsing, configuration and explicit incomplete-stage behaviour."""

from pathlib import Path

import pytest

from baltic_sales_intelligence.pipeline.cli import ExitCode, build_parser, main

CONFIG_DIRECTORY = Path(__file__).parents[2] / "config"


def test_parser_rejects_unknown_stage() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--config", str(CONFIG_DIRECTORY), "--stage", "unknown"])
    assert exc_info.value.code == ExitCode.USAGE_ERROR


def test_unimplemented_stage_returns_explicit_nonzero_exit() -> None:
    result = main(["--config", str(CONFIG_DIRECTORY), "--stage", "generate"])
    assert result == ExitCode.NOT_IMPLEMENTED


def test_reserved_flags_return_nonzero() -> None:
    result = main(
        ["--config", str(CONFIG_DIRECTORY), "--stage", "validate", "--dry-run"]
    )
    assert result == ExitCode.NOT_IMPLEMENTED


def test_missing_config_returns_configuration_error(tmp_path: Path) -> None:
    result = main(["--config", str(tmp_path / "missing"), "--stage", "validate"])
    assert result == ExitCode.CONFIGURATION_ERROR
