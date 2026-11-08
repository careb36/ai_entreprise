"""Count the lines, words and characters of a text, like wc."""


def count(text: str) -> dict[str, int]:
    """Newline characters, whitespace-separated words and characters of *text*."""
    return {"lines": text.count("\n"), "words": len(text.split()), "chars": len(text)}
