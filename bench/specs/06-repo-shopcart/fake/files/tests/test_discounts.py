from decimal import Decimal

import pytest
from shopcart import Cart
from shopcart.discounts import discounted_total


def _cart(price):
    cart = Cart()
    cart.add("item", price)
    return cart


def test_save10_takes_ten_percent_off():
    assert discounted_total(_cart("20.00"), "SAVE10") == Decimal("18.00")


def test_fiveoff_stops_at_zero():
    assert discounted_total(_cart("2.00"), "FIVEOFF") == Decimal("0.00")


def test_no_code_is_the_subtotal():
    assert discounted_total(_cart("2.00"), None) == Decimal("2.00")


def test_an_unknown_code_is_named():
    with pytest.raises(ValueError, match="unknown discount code: X"):
        discounted_total(_cart("2.00"), "X")
