"""Compare repeated financial child scans with the precomputed lookup.

This standalone micro-benchmark uses generated in-memory records only. It is not
collected by pytest and does not read or write project business data.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable
from decimal import Decimal

import pandas as pd

from baltic_sales_intelligence.quality.processing import _financial_children_by_order

Fingerprint = tuple[int, Decimal, int]


def _synthetic_records(order_count: int, line_count: int) -> tuple[list[str], pd.DataFrame]:
    order_ids = [f"ORD-{index:06d}" for index in range(order_count)]
    lines = pd.DataFrame(
        {
            "order_id": [order_ids[index % order_count] for index in range(line_count)],
            "posted_net_sales_eur": [
                Decimal((index % 10) + 1).quantize(Decimal("0.01"))
                for index in range(line_count)
            ],
            "quantity_ordered": [(index % 5) + 1 for index in range(line_count)],
        }
    )
    return order_ids, lines


def _repeated_scan(order_ids: list[str], lines: pd.DataFrame) -> Fingerprint:
    line_total = 0
    net_total = Decimal("0.00")
    quantity_total = 0
    for order_id in order_ids:
        children = lines.loc[lines["order_id"] == order_id]
        net_sales = children["posted_net_sales_eur"].tolist()
        quantities = children["quantity_ordered"].tolist()
        line_total += len(net_sales)
        net_total += sum(net_sales, Decimal("0.00"))
        quantity_total += sum(quantities)
    return line_total, net_total, quantity_total


def _precomputed_lookup(order_ids: list[str], lines: pd.DataFrame) -> Fingerprint:
    children_by_order = _financial_children_by_order(lines)
    line_total = 0
    net_total = Decimal("0.00")
    quantity_total = 0
    for order_id in order_ids:
        net_sales, quantities = children_by_order.get(order_id, ([], []))
        line_total += len(net_sales)
        net_total += sum(net_sales, Decimal("0.00"))
        quantity_total += sum(quantities)
    return line_total, net_total, quantity_total


def _timings(operation: Callable[[], Fingerprint], repeats: int) -> tuple[list[float], Fingerprint]:
    durations = []
    result: Fingerprint | None = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        durations.append(time.perf_counter() - started)
    assert result is not None
    return durations, result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=5_000)
    parser.add_argument("--lines", type=int, default=15_000)
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.orders < 1 or arguments.lines < 1 or arguments.repeats < 1:
        parser.error("orders, lines and repeats must all be positive")

    order_ids, lines = _synthetic_records(arguments.orders, arguments.lines)
    repeated_times, repeated_result = _timings(
        lambda: _repeated_scan(order_ids, lines), arguments.repeats
    )
    grouped_times, grouped_result = _timings(
        lambda: _precomputed_lookup(order_ids, lines), arguments.repeats
    )
    if repeated_result != grouped_result:
        raise RuntimeError("lookup methods produced different fingerprints")

    repeated_median = statistics.median(repeated_times)
    grouped_median = statistics.median(grouped_times)
    print(f"synthetic records: {arguments.orders:,} orders, {arguments.lines:,} lines")
    print(f"repeated scan median: {repeated_median:.6f}s")
    print(f"precomputed lookup median: {grouped_median:.6f}s")
    print(f"speedup: {repeated_median / grouped_median:.1f}x")
    print(f"verified fingerprint: {repeated_result}")


if __name__ == "__main__":
    main()
