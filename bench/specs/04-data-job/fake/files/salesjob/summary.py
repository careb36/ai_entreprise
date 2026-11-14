"""Sum the sales of a CSV text per region, exactly, and list the rows left out."""

import csv
import io
from decimal import Decimal, InvalidOperation

COLUMNS = ("region", "product", "amount")
CENT = Decimal("0.01")


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _amount(raw: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"amount {raw!r} is not a number") from None
    if not value.is_finite():
        raise ValueError(f"amount {raw!r} is not a number")
    if value < 0:
        raise ValueError(f"amount {raw!r} is negative")
    return value


def summarize(text: str) -> dict:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    names = [h.strip().lower() for h in header or []]
    missing = [c for c in COLUMNS if c not in names]
    if missing:
        raise ValueError(f"the header has no {', '.join(missing)} column")
    where = {c: names.index(c) for c in COLUMNS}
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    bad: list[dict] = []
    for row in reader:
        line = reader.line_num
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(names):
            bad.append(
                {
                    "line": line,
                    "reason": f"expected {len(names)} fields, found {len(row)}",
                }
            )
            continue
        region = row[where["region"]].strip()
        if not region:
            bad.append({"line": line, "reason": "the region is empty"})
            continue
        try:
            amount = _amount(row[where["amount"]])
        except ValueError as exc:
            bad.append({"line": line, "reason": str(exc)})
            continue
        totals[region] = totals.get(region, Decimal(0)) + amount
        counts[region] = counts.get(region, 0) + 1
    return {
        "regions": {
            r: {"count": counts[r], "total": _money(totals[r])} for r in sorted(totals)
        },
        "total": _money(sum(totals.values(), Decimal(0))),
        "rows": sum(counts.values()),
        "bad": bad,
    }
