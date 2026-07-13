from decimal import Decimal

from mini_multi.formatting import format_receipt
from mini_multi.pricing import total_price


def test_decimal_price_and_receipt_are_stable() -> None:
    subtotal, tax, total = total_price(Decimal("0.10"), 3, Decimal("0.075"))
    assert (subtotal, tax, total) == (Decimal("0.30"), Decimal("0.02"), Decimal("0.32"))
    assert format_receipt(subtotal, tax, total) == (
        "subtotal=0.30\ntax=0.02\ntotal=0.32"
    )
