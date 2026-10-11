"""Pipeline CLI with deterministic canonical truth generation."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from enum import IntEnum, StrEnum
from pathlib import Path

from baltic_sales_intelligence.config import ConfigurationError, load_configuration
from baltic_sales_intelligence.generation import generate_reference_data
from baltic_sales_intelligence.logging import configure_logging, log_event
from baltic_sales_intelligence.quality import publish_raw_injection


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
            "Run a versioned Baltic Sales Intelligence pipeline stage. "
            "Generate canonical truth or inject deterministic defects into raw copies."
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
        help="Run a supported stage without publishing outputs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing supported-stage output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Stage output directory (defaults to data/truth or data/raw).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    supported = {Stage.GENERATE, Stage.INJECT_DEFECTS}
    if (args.dry_run or args.force or args.output) and args.stage not in supported:
        parser = build_parser()
        parser.print_usage()
        print(
            "error: --dry-run, --force and --output are supported only for "
            "generate and inject-defects",
            flush=True,
        )
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
    if args.stage == Stage.GENERATE:
        output = args.output or (Path(args.config).resolve().parent / "data" / "truth")
        manifest_path = output / "generation_manifest.json"
        if manifest_path.is_file() and not args.force and not args.dry_run:
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if existing.get("config_hash") != configuration.deterministic_hash():
                log_event(
                    logger,
                    logging.ERROR,
                    "existing generated truth has a different config hash; use --force",
                    stage=args.stage,
                    action="publish",
                    dataset="reference_truth",
                    error_category="OUTPUT_CONFLICT",
                )
                return ExitCode.STAGE_FAILED
        try:
            result = generate_reference_data(configuration, output, publish=not args.dry_run)
        except (OSError, ValueError) as exc:
            log_event(
                logger,
                logging.ERROR,
                str(exc),
                stage=args.stage,
                action="generation",
                dataset="reference_truth",
                batch_id=configuration.deterministic_hash()[:12],
                error_category="GENERATION_FAILED",
            )
            return ExitCode.STAGE_FAILED
        log_event(
            logger,
            logging.INFO,
            "canonical truth validated" if args.dry_run else "canonical truth published",
            stage=args.stage,
            action="dry_run" if args.dry_run else "publish",
            dataset="canonical_truth",
            batch_id=configuration.deterministic_hash()[:12],
            count=sum(item["row_count"] for item in result.manifest["datasets"].values()),
        )
        return ExitCode.OK
    if args.stage == Stage.INJECT_DEFECTS:
        project_root = Path(args.config).resolve().parent
        truth = project_root / "data" / "truth"
        output = args.output or (project_root / "data" / "raw")
        registry_output = output.parent / "qa" / "injections"
        manifest_path = output / "raw_injection_manifest.json"
        if manifest_path.is_file() and not args.force and not args.dry_run:
            log_event(
                logger,
                logging.ERROR,
                "raw injection output already exists; use --force",
                stage=args.stage,
                action="publish",
                dataset="raw_copies",
                error_category="OUTPUT_CONFLICT",
            )
            return ExitCode.STAGE_FAILED
        try:
            result = publish_raw_injection(
                configuration,
                truth,
                output,
                registry_output,
                publish=not args.dry_run,
            )
        except (OSError, ValueError) as exc:
            log_event(
                logger,
                logging.ERROR,
                str(exc),
                stage=args.stage,
                action="injection",
                dataset="raw_copies",
                batch_id=configuration.deterministic_hash()[:12],
                error_category="INJECTION_FAILED",
            )
            return ExitCode.STAGE_FAILED
        log_event(
            logger,
            logging.INFO,
            "raw defects prepared" if args.dry_run else "raw defects published",
            stage=args.stage,
            action="dry_run" if args.dry_run else "publish",
            dataset="raw_copies",
            batch_id=result.manifest["batch_id"],
            count=len(result.registry),
        )
        return ExitCode.OK
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
