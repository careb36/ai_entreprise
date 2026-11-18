from decimal import Decimal

import pytest
from shopcart import Cart


def test_the_total_sums_price_times_quantity():
    cart = Cart()
    cart.add("tea", "2.50", 2)
    cart.add("cake", Decimal("3.10"))
    assert cart.total() == Decimal("8.10")


def test_adding_a_sku_again_adds_to_its_quantity():
    cart = Cart()
    cart.add("tea", "2.50")
    cart.add("tea", "2.50", 3)
    assert cart.lines() == [("tea", Decimal("2.50"), 4)]


def test_a_sku_keeps_its_price():
    cart = Cart()
    cart.add("tea", "2.50")
    with pytest.raises(ValueError):
        cart.add("tea", "2.60")


@pytest.mark.parametrize("price", ["-1", "abc", "NaN"])
def test_a_bad_price_is_refused(price):
    with pytest.raises(ValueError):
        Cart().add("tea", price)


def test_removing_part_of_a_line_lowers_its_quantity():
    cart = Cart()
    cart.add("tea", "2.50", 3)
    cart.remove("tea", 2)
    assert cart.lines() == [("tea", Decimal("2.50"), 1)]
    assert cart.total() == Decimal("2.50")


def test_an_empty_cart_totals_zero():
    assert Cart().total() == Decimal("0.00")
