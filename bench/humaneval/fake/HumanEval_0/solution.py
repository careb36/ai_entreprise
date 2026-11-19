from itertools import pairwise


def has_close_elements(numbers: list[float], threshold: float) -> bool:
    """Whether two numbers of the list lie closer to each other than threshold."""
    return any(b - a < threshold for a, b in pairwise(sorted(numbers)))
