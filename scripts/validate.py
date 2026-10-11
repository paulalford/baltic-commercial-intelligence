"""Thin wrapper for the future validation stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.VALIDATE, *sys.argv[1:]]))
