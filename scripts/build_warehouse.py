"""Thin wrapper for the DuckDB warehouse stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.WAREHOUSE, *sys.argv[1:]]))
