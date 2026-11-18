from decimal import Decimal

import pytest
from shopcart import Cart


def test_removing_too_much_leaves_the_cart_alone():
    cart = Cart()
    cart.add("tea", "2.50", 2)
    with pytest.raises(ValueError):
        cart.remove("tea", 3)
    assert cart.lines() == [("tea", Decimal("2.50"), 2)]


def test_removing_everything_drops_the_line():
    cart = Cart()
    cart.add("tea", "2.50", 2)
    cart.remove("tea", 2)
    assert cart.lines() == []
    assert cart.total() == Decimal("0.00")
