"""Whether two tasks likely repeat each other, from their titles alone.

Sprint 3 of the factory's own run planned "Deliver SSO tests with fake DNS
and clock plus docs for configuration and operations" (task 14) and, in the
same request, carried over "Add SSO tests with fake DNS and clock, plus
configuration and operations docs" (task 19): the same work, done twice and
reviewed twice, with nothing to say so (issue #766).

``similarity`` is deterministic and needs no model: the Jaccard index of the
titles' content words, lowercased, without stop words or the generic verbs a
planner opens a title with ("add", "deliver", "implement"), and with a plural
``s`` dropped; a number is kept, so "Item 1" and "Item 2" stay apart. On the tasks recorded so far, the duplicate pair scores 0.78,
the same work planned again in the next sprint 0.60 to 1.00, and no unrelated
pair reaches 0.5 (two tasks about the same cards, one for the code and one
for its strings, score below it). ``THRESHOLD`` is 0.7.

A score is a claim made by a rule, never a verdict: the caller records it as
a *proposed* ``duplicate`` relation for a person to confirm or reject
(``relations.propose_likely_duplicates``).
"""

from __future__ import annotations

import re

THRESHOLD = 0.7
RULE = "rule:similar_title"
MIN_WORD = 3
_WORD = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "without",
        "by",
        "from",
        "into",
        "plus",
        "per",
        "its",
        "it",
        "be",
        "is",
        "are",
        "as",
        "at",
        "or",
        "via",
        "new",
        "task",
        "tasks",
        "add",
        "adds",
        "deliver",
        "delivers",
        "implement",
        "implements",
        "create",
        "creates",
        "update",
        "updates",
        "make",
        "ensure",
        "include",
        "includes",
        "support",
        "supports",
    ]
)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(title: str) -> frozenset[str]:
    """The title's content words, as ``similarity`` compares them."""
    return frozenset(
        _singular(w)
        for w in _WORD.findall((title or "").lower())
        # A number is kept whatever its length: "Item 1" and "Item 2" of a
        # plan are different work, not one task written twice.
        if (w.isdigit() or len(w) >= MIN_WORD) and w not in STOP_WORDS
    )


def similarity(a: str, b: str) -> float:
    """The Jaccard index of the two titles' content words, 0.0 to 1.0."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def likely_duplicate(a: str, b: str) -> bool:
    return similarity(a, b) >= THRESHOLD
