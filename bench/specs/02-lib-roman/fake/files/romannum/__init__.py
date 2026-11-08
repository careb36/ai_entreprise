"""Integers to Roman numerals and back, standard notation, 1 to 3999."""

_SYMBOLS = (
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
)
_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def to_roman(n: int) -> str:
    if not isinstance(n, int) or not 1 <= n <= 3999:
        raise ValueError(f"{n!r} is outside 1 to 3999")
    out = []
    for value, symbol in _SYMBOLS:
        times, n = divmod(n, value)
        out.append(symbol * times)
    return "".join(out)


def from_roman(s: str) -> int:
    if not s or any(ch not in _VALUES for ch in s):
        raise ValueError(f"{s!r} is not a Roman numeral")
    total = 0
    for current, following in zip(s, s[1:] + " "):
        value = _VALUES[current]
        total += -value if following != " " and _VALUES[following] > value else value
    # Only the standard spelling reads back to itself (IIII, IC are refused).
    if not 1 <= total <= 3999 or to_roman(total) != s:
        raise ValueError(f"{s!r} is not in standard notation")
    return total
