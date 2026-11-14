"""python -m salesjob <input.csv> [--format json|csv]: the sales per region."""

import argparse
import csv
import json
import sys

from salesjob.summary import summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="salesjob")
    parser.add_argument("input")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args(argv)
    try:
        with open(args.input, encoding="utf-8", newline="") as handle:
            text = handle.read()
        result = summarize(text)
    except (OSError, UnicodeDecodeError) as exc:
        print(
            f"salesjob: {args.input}: {getattr(exc, 'strerror', None) or exc}",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"salesjob: {args.input}: {exc}", file=sys.stderr)
        return 1
    for row in result["bad"]:
        print(f"line {row['line']}: {row['reason']}", file=sys.stderr)
    if args.format == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["region", "count", "total"])
        for region, numbers in sorted(result["regions"].items()):
            writer.writerow([region, numbers["count"], numbers["total"]])
    else:
        summary = {k: result[k] for k in ("regions", "total", "rows")}
        summary["bad_rows"] = len(result["bad"])
        print(json.dumps(summary, indent=2))
    if result["bad"]:
        print(f"{len(result['bad'])} bad rows", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
