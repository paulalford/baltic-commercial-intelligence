"""Thin wrapper for the future defect-injection stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.INJECT_DEFECTS, *sys.argv[1:]]))
