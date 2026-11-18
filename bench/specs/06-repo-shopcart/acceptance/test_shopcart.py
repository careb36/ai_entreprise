"""Acceptance tests of the shopping cart change, written by a person.

The project starts from the seed in ``../seed`` (an existing repository the
factory did not write); these tests grade the change the specification asks
for. The model under test never sees or edits them. Each test is named after
the criterion it decides (``test_req_1_1_...`` decides REQ-1.1); a criterion
passes when every test named after it passes. The project under test is on
``PYTHONPATH``.

Each criterion also checks that what the seed already did still works (a
partial removal, the total without a code, the command line without
``--code``), so a change that breaks the existing code fails the criterion it
touches. None of them passes on the seed alone.
"""

import json
import subprocess
import sys
from decimal import Decimal

import pytest


def _cart(*lines):
    from shopcart import Cart

    cart = Cart()
    for sku, price, quantity in lines:
        cart.add(sku, price, quantity)
    return cart


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shopcart", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _cart_file(tmp_path):
    path = tmp_path / "cart.json"
    lines = [
        {"sku": "tea", "price": "2.50", "quantity": 4},
        {"sku": "cake", "price": "40.00"},
    ]
    path.write_text(json.dumps(lines), encoding="utf-8")
    return path


# REQ-1: Remove items from the cart safely


def test_req_1_1_removing_more_than_held_is_refused():
    cart = _cart(("tea", "2.50", 2))
    with pytest.raises(ValueError):
        cart.remove("tea", 3)
    assert cart.lines() == [("tea", Decimal("2.50"), 2)]
    assert cart.total() == Decimal("5.00")


def test_req_1_1_partial_and_unknown_removals_behave_as_before():
    cart = _cart(("tea", "2.50", 3))
    cart.remove("tea", 2)
    assert cart.lines() == [("tea", Decimal("2.50"), 1)]
    with pytest.raises(KeyError):
        cart.remove("cake")
    # A refused removal of a line already lowered leaves it as it is.
    with pytest.raises(ValueError):
        cart.remove("tea", 2)
    assert cart.lines() == [("tea", Decimal("2.50"), 1)]


def test_req_1_2_removing_a_whole_line_drops_it():
    cart = _cart(("tea", "2.50", 2), ("cake", "3.10", 1))
    cart.remove("tea", 2)
    assert cart.lines() == [("cake", Decimal("3.10"), 1)]
    assert cart.total() == Decimal("3.10")
    # The line is gone: removing it again is a SKU not in the cart.
    with pytest.raises(KeyError):
        cart.remove("tea")


# REQ-2: Apply a discount code to the total


def test_req_2_1_a_percent_code_rounds_half_up():
    from shopcart.discounts import discounted_total

    fifty = _cart(("tea", "2.50", 4), ("cake", "40.00", 1))
    odd = _cart(("tea", "33.35", 1))
    assert discounted_total(fifty, "SAVE10") == Decimal("45.00")
    assert discounted_total(odd, "SAVE10") == Decimal("30.01")
    assert discounted_total(fifty, None) == Decimal("50.00")
    assert discounted_total(odd, None) == Decimal("33.35")
    # The cart's own total is not changed by asking for a discounted one.
    assert fifty.total() == Decimal("50.00")


def test_req_2_1_the_known_codes_are_listed():
    from shopcart.discounts import CODES

    assert {"SAVE10", "FIVEOFF"} <= set(CODES)


def test_req_2_2_a_fixed_code_never_goes_below_zero():
    from shopcart.discounts import discounted_total

    assert discounted_total(_cart(("tea", "4.00", 3)), "FIVEOFF") == Decimal("7.00")
    assert discounted_total(_cart(("tea", "3.00", 1)), "FIVEOFF") == Decimal("0.00")


@pytest.mark.parametrize("code", ["NOPE", "save10"])
def test_req_2_3_an_unknown_code_is_refused_by_name(code):
    from shopcart.discounts import discounted_total

    with pytest.raises(ValueError) as raised:
        discounted_total(_cart(("tea", "50.00", 1)), code)
    assert str(raised.value) == f"unknown discount code: {code}"


# REQ-3: Apply a discount code from the command line


def test_req_3_1_the_code_option_takes_the_discount_off(tmp_path):
    path = _cart_file(tmp_path)
    with_code = _run(str(path), "--code", "SAVE10")
    assert (with_code.returncode, with_code.stdout) == (0, "Total: 45.00\n"), (
        with_code.stderr
    )
    without = _run(str(path))
    assert (without.returncode, without.stdout) == (0, "Total: 50.00\n"), without.stderr


def test_req_3_1_a_bad_file_still_exits_1(tmp_path):
    run = _run(str(tmp_path / "none.json"), "--code", "SAVE10")
    assert run.returncode == 1 and run.stdout == ""


def test_req_3_2_an_unknown_code_exits_2(tmp_path):
    run = _run(str(_cart_file(tmp_path)), "--code", "NOPE")
    assert run.returncode == 2
    assert run.stdout == ""
    assert "unknown discount code: NOPE" in run.stderr
