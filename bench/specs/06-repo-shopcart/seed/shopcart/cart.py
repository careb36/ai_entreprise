"""The cart: lines of a SKU, a unit price and a quantity."""

from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")


def to_price(value) -> Decimal:
    """A price as an exact decimal; ValueError when it is not one or is negative."""
    try:
        price = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"not a price: {value!r}") from None
    if not price.is_finite() or price < 0:
        raise ValueError(f"not a price: {value!r}")
    return price


class Cart:
    def __init__(self) -> None:
        self._lines: dict[str, list] = {}  # sku -> [unit price, quantity]

    def add(self, sku: str, price, quantity: int = 1) -> None:
        if quantity < 1:
            raise ValueError("the quantity must be at least 1")
        price = to_price(price)
        line = self._lines.get(sku)
        if line is None:
            self._lines[sku] = [price, quantity]
        elif line[0] != price:
            raise ValueError(f"{sku} is already in the cart at {line[0]}")
        else:
            line[1] += quantity

    def remove(self, sku: str, quantity: int = 1) -> None:
        self._lines[sku][1] -= quantity

    def lines(self) -> list[tuple[str, Decimal, int]]:
        """(sku, unit price, quantity) per line, by SKU."""
        return [(sku, p, q) for sku, (p, q) in sorted(self._lines.items())]

    def subtotal(self) -> Decimal:
        return sum((p * q for p, q in self._lines.values()), Decimal(0)).quantize(CENT)

    def total(self) -> Decimal:
        return self.subtotal()
