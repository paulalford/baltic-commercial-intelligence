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
from baltic_sales_intelligence.quality import (
    DataQualityError,
    publish_raw_injection,
    publish_silver,
)
from baltic_sales_intelligence.warehouse import build_warehouse


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
            "Generate truth, inject defects, validate Silver, or build the DuckDB warehouse."
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
        "--input",
        type=Path,
        help="Stage input directory (validate: data/raw; warehouse: data/silver).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Stage output directory (defaults depend on the selected stage).",
    )
    parser.add_argument(
        "--quarantine-output",
        type=Path,
        help="Validate-stage quarantine directory (defaults to data/quarantine).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.quarantine_output and args.stage != Stage.VALIDATE:
        parser = build_parser()
        parser.print_usage()
        print("error: --quarantine-output is supported only for validate", flush=True)
        return ExitCode.USAGE_ERROR

    supported = {Stage.GENERATE, Stage.INJECT_DEFECTS, Stage.VALIDATE, Stage.WAREHOUSE}
    if (
        args.dry_run or args.force or args.input or args.output or args.quarantine_output
    ) and args.stage not in supported:
        parser = build_parser()
        parser.print_usage()
        print(
            "error: stage path and publication flags are not supported for this later stage",
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
    if args.stage == Stage.VALIDATE:
        project_root = Path(args.config).resolve().parent
        source = args.input or (project_root / "data" / "raw")
        output = args.output or (project_root / "data" / "silver")
        quarantine = args.quarantine_output or (project_root / "data" / "quarantine")
        try:
            result = publish_silver(
                configuration,
                source,
                output,
                quarantine,
                publish=not args.dry_run,
                force=args.force,
            )
        except (OSError, ValueError, DataQualityError) as exc:
            log_event(
                logger,
                logging.ERROR,
                str(exc),
                stage=args.stage,
                action="validation",
                dataset="raw_batch",
                batch_id=configuration.deterministic_hash()[:12],
                error_category="VALIDATION_FAILED",
            )
            return ExitCode.STAGE_FAILED
        run_counts = result.dispositions.loc[result.dispositions["scope"] == "run"].iloc[0]
        log_event(
            logger,
            logging.INFO,
            "raw batch validated" if args.dry_run else "Silver data published",
            stage=args.stage,
            action="dry_run" if args.dry_run else "publish",
            dataset="silver",
            batch_id=result.manifest["source_batch_id"],
            count=int(run_counts["accepted_unchanged"] + run_counts["repaired"]),
        )
        for action, column in (
            ("quarantine", "quarantined"),
            ("repair", "repaired"),
            ("discard_exact_duplicate", "discarded_exact_duplicate"),
        ):
            log_event(
                logger,
                logging.INFO,
                f"validation disposition count: {action}",
                stage=args.stage,
                action=action,
                dataset="raw_batch",
                batch_id=result.manifest["source_batch_id"],
                count=int(run_counts[column]),
            )
        return ExitCode.OK
    if args.stage == Stage.WAREHOUSE:
        project_root = Path(args.config).resolve().parent
        source = args.input or (project_root / "data" / "silver")
        output = args.output or (project_root / "data" / "database")
        try:
            result = build_warehouse(
                configuration,
                source,
                output,
                publish=not args.dry_run,
                force=args.force,
            )
        except (OSError, ValueError) as exc:
            log_event(
                logger,
                logging.ERROR,
                str(exc),
                stage=args.stage,
                action="warehouse_build",
                dataset="silver",
                batch_id=configuration.deterministic_hash()[:12],
                error_category="WAREHOUSE_FAILED",
            )
            return ExitCode.STAGE_FAILED
        fact_count = sum(
            metadata["row_count"]
            for name, metadata in result.manifest["objects"].items()
            if name.startswith("fact_") and metadata["object_type"] == "table"
        )
        log_event(
            logger,
            logging.INFO,
            "warehouse validated" if args.dry_run else "warehouse published",
            stage=args.stage,
            action="dry_run" if args.dry_run else "publish",
            dataset="warehouse",
            batch_id=result.manifest["source_batch_id"],
            count=fact_count,
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
