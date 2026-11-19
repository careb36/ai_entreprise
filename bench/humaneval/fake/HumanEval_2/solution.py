import math


def truncate_number(number: float) -> float:
    """The decimal part of a positive number: what is left after its integer part."""
    return number - math.floor(number)
