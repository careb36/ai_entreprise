from decimal import Decimal

import pytest


def _tea(quantity):
    from shopcart import Cart

    cart = Cart()
    cart.add("tea", "2.50", quantity)
    return cart


def test_req_1_1_cannot_remove_more_than_held():
    cart = _tea(2)
    with pytest.raises(ValueError):
        cart.remove("tea", 3)
    assert cart.total() == Decimal("5.00")


def test_req_1_2_a_line_removed_in_full_is_gone():
    cart = _tea(2)
    cart.add("cake", "3.10")
    cart.remove("tea", 2)
    assert [sku for sku, _, _ in cart.lines()] == ["cake"]
