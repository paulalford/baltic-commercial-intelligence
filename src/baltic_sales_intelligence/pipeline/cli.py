"""Foundation CLI with explicit failure for every unimplemented stage."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from enum import IntEnum, StrEnum
from pathlib import Path

from baltic_sales_intelligence.config import ConfigurationError, load_configuration
from baltic_sales_intelligence.logging import configure_logging, log_event


class ExitCode(IntEnum):
    OK = 0
    USAGE_ERROR = 2
    CONFIGURATION_ERROR = 3
    NOT_IMPLEMENTED = 4
    STAGE_FAILED = 5


class Stage(StrEnum):
    GENERATE = "generate"
    INJECT_DEFECTS = "inject-defects"
    VALIDATE = "validate"
    WAREHOUSE = "warehouse"
    ANALYTICS = "analytics"
    INSIGHTS = "insights"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m baltic_sales_intelligence.pipeline",
        description=(
            "Validate foundation configuration and select a future pipeline stage. "
            "Milestone 1 does not implement business-processing stages."
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Directory containing the four versioned YAML configuration files.",
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=[stage.value for stage in Stage],
        help="Pipeline stage to request.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Reserved for a later milestone; currently returns a nonzero error.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reserved for a later milestone; currently returns a nonzero error.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run or args.force:
        parser = build_parser()
        parser.print_usage()
        print("error: --dry-run and --force are reserved and not implemented", flush=True)
        return ExitCode.NOT_IMPLEMENTED

    try:
        configuration = load_configuration(args.config)
    except ConfigurationError as exc:
        logger = configure_logging("ERROR")
        log_event(
            logger,
            logging.ERROR,
            str(exc),
            stage=args.stage,
            action="configuration_validation",
            error_category="CONFIGURATION_ERROR",
        )
        return ExitCode.CONFIGURATION_ERROR

    logger = configure_logging(configuration.application.logging.level)
    log_event(
        logger,
        logging.ERROR,
        f"NOT_IMPLEMENTED: stage '{args.stage}' is reserved for a later milestone",
        stage=args.stage,
        action="stage_dispatch",
        batch_id=configuration.deterministic_hash()[:12],
        error_category="NOT_IMPLEMENTED",
    )
    return ExitCode.NOT_IMPLEMENTED


def entrypoint() -> None:
    """Installed console-script entry point."""

    raise SystemExit(main())
