"""The one rule for comparing email addresses: strip the spaces around it and
lower-case it.

``Ana@Example.com`` and ``ana@example.com`` are one account. The whole address
is lower-cased, the local part too: RFC 5321 lets a server treat the local
part as case-sensitive, but no provider a person signs up with does, and two
accounts that differ only in case are a takeover waiting to happen. Nothing
else is rewritten (no dot or ``+tag`` folding, no Unicode normalisation), so
the rule stays predictable. Registration, sign-in, invitations and every
lookup by address go through it; migration 0045 applied it to the rows
already stored, and a unique index on ``lower(email)`` keeps it.
"""

from __future__ import annotations


def normalize_email(value: str) -> str:
    """*value* stripped of surrounding whitespace and lower-cased."""
    return value.strip().lower()
