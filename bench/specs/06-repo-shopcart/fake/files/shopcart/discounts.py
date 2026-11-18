"""Discount codes: a percent or a fixed amount off the cart's subtotal."""

from decimal import ROUND_HALF_UP, Decimal

from shopcart.cart import CENT, Cart

# code -> (kind, value): "percent" takes value percent off, "fixed" value off.
CODES: dict[str, tuple[str, Decimal]] = {
    "SAVE10": ("percent", Decimal(10)),
    "FIVEOFF": ("fixed", Decimal("5.00")),
}


def discount_of(code: str, subtotal: Decimal) -> Decimal:
    if code not in CODES:
        raise ValueError(f"unknown discount code: {code}")
    kind, value = CODES[code]
    if kind == "percent":
        return (subtotal * value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return value


def discounted_total(cart: Cart, code: str | None) -> Decimal:
    subtotal = cart.subtotal()
    if code is None:
        return subtotal
    return max(subtotal - discount_of(code, subtotal), Decimal(0)).quantize(CENT)
