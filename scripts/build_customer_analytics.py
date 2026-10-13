"""Thin wrapper for the customer-analytics stage."""

import sys

from baltic_sales_intelligence.pipeline.cli import Stage, main

raise SystemExit(main(["--stage", Stage.CUSTOMER, *sys.argv[1:]]))
