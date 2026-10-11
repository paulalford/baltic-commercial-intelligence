"""Thin wrapper for deterministic reference generation."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.GENERATE, *sys.argv[1:]]))
