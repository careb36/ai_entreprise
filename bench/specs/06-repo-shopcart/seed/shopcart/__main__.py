"""python -m shopcart CART.json: print the cart's total.

Exit 0 with "Total: <amount>" on stdout, 1 when the file cannot be read or
holds a line that is not valid.
"""

import argparse
import json
import sys

from shopcart.cart import Cart


def load(path: str) -> Cart:
    with open(path, encoding="utf-8") as handle:
        lines = json.load(handle)
    if not isinstance(lines, list):
        raise TypeError("the cart file must hold a list of lines")
    cart = Cart()
    for line in lines:
        cart.add(line["sku"], line["price"], int(line.get("quantity", 1)))
    return cart


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shopcart")
    parser.add_argument("cart", help="a JSON file holding the cart's lines")
    args = parser.parse_args(argv)
    try:
        cart = load(args.cart)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Total: {cart.total()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
