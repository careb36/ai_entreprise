from decimal import Decimal

import pytest


def _cart(price):
    from shopcart import Cart

    cart = Cart()
    cart.add("item", price)
    return cart


def test_req_2_1_save10():
    from shopcart.discounts import discounted_total

    assert discounted_total(_cart("50.00"), "SAVE10") == Decimal("45.00")
    assert discounted_total(_cart("33.35"), "SAVE10") == Decimal("30.01")


def test_req_2_2_fiveoff():
    from shopcart.discounts import discounted_total

    assert discounted_total(_cart("12.00"), "FIVEOFF") == Decimal("7.00")
    assert discounted_total(_cart("3.00"), "FIVEOFF") == Decimal("0.00")


def test_req_2_3_unknown_code():
    from shopcart.discounts import discounted_total

    with pytest.raises(ValueError, match="unknown discount code: NOPE"):
        discounted_total(_cart("50.00"), "NOPE")
