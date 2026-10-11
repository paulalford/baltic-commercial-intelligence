"""Gold fixtures for SPEC-04 sale, return and freight arithmetic."""

from decimal import Decimal

import pytest

from baltic_sales_intelligence.generation.financial import (
    allocate_freight,
    return_postings,
    sale_postings,
)


def test_ct_fin_001_fulfilment_posting_round_half_even() -> None:
    assert sale_postings(
        3,
        Decimal("10.0050"),
        Decimal("0.100000"),
        Decimal("6.3333"),
        Decimal("0.200000"),
        fulfilled=True,
    ) == (
        Decimal("30.02"),
        Decimal("3.00"),
        Decimal("27.02"),
        Decimal("19.00"),
        Decimal("27.02"),
        Decimal("5.40"),
        Decimal("32.42"),
    )


@pytest.mark.parametrize("fulfilled", [False])
def test_ct_fin_002_nonfulfilled_lines_post_zero(fulfilled: bool) -> None:
    assert (
        sale_postings(
            3,
            Decimal("10.0050"),
            Decimal("0.100000"),
            Decimal("6.3333"),
            Decimal("0.200000"),
            fulfilled=fulfilled,
        )
        == (Decimal("0.00"),) * 7
    )


def test_ct_fin_003_and_004_return_economics() -> None:
    common = (1, Decimal("9.0045"), Decimal("9.0045"), Decimal("6.3333"), Decimal("0.200000"))
    assert return_postings(*common, "restockable", accepted=True) == (
        Decimal("9.00"),
        Decimal("6.33"),
        Decimal("1.80"),
    )
    assert return_postings(*common, "written_off", accepted=True) == (
        Decimal("9.00"),
        Decimal("0.00"),
        Decimal("1.80"),
    )


def test_return_refund_cannot_exceed_original_realised_price() -> None:
    with pytest.raises(ValueError, match="exceeds original"):
        return_postings(
            1,
            Decimal("9.0046"),
            Decimal("9.0045"),
            Decimal("6.3333"),
            Decimal("0.200000"),
            "restockable",
            accepted=True,
        )


def test_ct_fin_009_freight_allocation_and_quantity_fallback() -> None:
    assert allocate_freight(Decimal("5.00"), [Decimal("27.02"), Decimal("12.98")], [3, 2]) == [
        Decimal("3.38"),
        Decimal("1.62"),
    ]
    assert allocate_freight(Decimal("0.03"), [Decimal("0.00"), Decimal("0.00")], [1, 2]) == [
        Decimal("0.01"),
        Decimal("0.02"),
    ]
