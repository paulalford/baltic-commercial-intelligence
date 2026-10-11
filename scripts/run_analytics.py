"""Thin wrapper for the future analytics stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.ANALYTICS, *sys.argv[1:]]))
