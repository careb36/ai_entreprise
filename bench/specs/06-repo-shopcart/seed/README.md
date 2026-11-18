# shopcart

A small shopping cart library and command line, standard library only.

```python
from shopcart import Cart

cart = Cart()
cart.add("tea", "2.50", 2)
cart.add("cake", "3.10")
cart.remove("tea")
cart.total()  # Decimal('5.60')
```

```bash
python -m shopcart cart.json   # prints "Total: 5.60"
```

`cart.json` is a list of lines, each `{"sku": "tea", "price": "2.50",
"quantity": 2}`; the quantity defaults to 1. Prices are exact decimals.

Run the tests from the project root with `python -m pytest`.
