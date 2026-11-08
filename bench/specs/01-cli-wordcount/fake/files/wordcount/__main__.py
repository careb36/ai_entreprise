"""python -m wordcount <file>: print the file's lines, words and characters."""

import sys

from wordcount import count


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m wordcount <file>", file=sys.stderr)
        return 2
    path = args[0]
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"wordcount: {path}: {exc.strerror}", file=sys.stderr)
        return 1
    counts = count(text)
    print(f"{counts['lines']} {counts['words']} {counts['chars']} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
