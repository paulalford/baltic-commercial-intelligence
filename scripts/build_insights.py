"""Thin wrapper for the future insight stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.INSIGHTS, *sys.argv[1:]]))
