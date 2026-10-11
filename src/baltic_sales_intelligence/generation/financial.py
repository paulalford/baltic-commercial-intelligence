"""Exact SPEC-04 posting arithmetic shared by generation and contract tests."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")
UNIT = Decimal("0.0001")
RATE = Decimal("0.000001")
ZERO = Decimal("0.00")


def quantize_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def quantize_unit(value: Decimal) -> Decimal:
    return value.quantize(UNIT, rounding=ROUND_HALF_EVEN)


def sale_postings(
    quantity: int,
    list_unit_price: Decimal,
    discount_rate: Decimal,
    unit_cost: Decimal,
    vat_rate: Decimal,
    *,
    fulfilled: bool,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return list, discount, net, COGS, taxable base, VAT and invoice postings."""

    if not fulfilled:
        return (ZERO,) * 7
    unrounded_list = Decimal(quantity) * list_unit_price
    posted_list = quantize_cent(unrounded_list)
    posted_discount = quantize_cent(unrounded_list * discount_rate)
    posted_net = posted_list - posted_discount
    posted_cogs = quantize_cent(Decimal(quantity) * unit_cost)
    taxable_base = posted_net
    posted_vat = quantize_cent(taxable_base * vat_rate)
    invoice_total = taxable_base + posted_vat
    return (
        posted_list,
        posted_discount,
        posted_net,
        posted_cogs,
        taxable_base,
        posted_vat,
        invoice_total,
    )


def return_postings(
    accepted_quantity: int | None,
    refund_unit_ex_vat: Decimal | None,
    original_realised_unit_price: Decimal,
    original_unit_cost: Decimal,
    original_vat_rate: Decimal,
    restocking_outcome: str | None,
    *,
    accepted: bool,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return nonnegative refund, recovered COGS and VAT-reversal magnitudes."""

    if not accepted:
        return ZERO, ZERO, ZERO
    if accepted_quantity is None or refund_unit_ex_vat is None:
        raise ValueError("accepted return requires quantity and refund unit value")
    if refund_unit_ex_vat > original_realised_unit_price:
        raise ValueError("refund unit value exceeds original realised unit price")
    refund = quantize_cent(Decimal(int(accepted_quantity)) * refund_unit_ex_vat)
    cap = quantize_cent(Decimal(int(accepted_quantity)) * original_realised_unit_price)
    if refund > cap:
        raise ValueError("posted refund exceeds original realised-price cap")
    recovered = (
        quantize_cent(Decimal(int(accepted_quantity)) * original_unit_cost)
        if restocking_outcome == "restockable"
        else ZERO
    )
    return refund, recovered, quantize_cent(refund * original_vat_rate)


def allocate_freight(
    freight: Decimal,
    net_sales: list[Decimal],
    quantities: list[int],
) -> list[Decimal]:
    """Allocate order freight to lines and put the cent remainder on the final line."""

    if freight < ZERO or len(net_sales) != len(quantities) or not net_sales:
        raise ValueError("invalid freight-allocation inputs")
    weights = net_sales if sum(net_sales, ZERO) != ZERO else [Decimal(q) for q in quantities]
    total_weight = sum(weights, ZERO)
    if total_weight <= ZERO:
        raise ValueError("freight allocation requires positive quantity")
    allocations = [quantize_cent(freight * weight / total_weight) for weight in weights[:-1]]
    allocations.append(freight - sum(allocations, ZERO))
    return allocations
