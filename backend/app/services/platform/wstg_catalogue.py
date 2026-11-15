"""The OWASP Web Security Testing Guide as a versioned catalogue (issue #539).

``app/data/wstg/catalogue.json`` is built by ``scripts/wstg_catalogue.py`` from
the guide's ``checklists/checklist.json`` at a pinned commit. It is a
derivative of the guide and stays under **CC BY-SA 4.0**, with the attribution
inside the file and in ``NOTICE``; this module (Apache-2.0) only reads it.

Every test carries a versioned identifier, the scheme the security probe uses
(``services/evidence/security_probe.py``, issue #540): ``WSTG-v42-CONF-06``
when release 4.2 has the same test under that identifier, else
``WSTG-<commit7>-CONF-12``. ``versioned`` turns a short id (``CONF-12``) into
it, so a test QA names ``test_wstg_conf_12_…`` and a probe item cite the same
row.

Read once and cached; the file ships with the backend and never changes at run time.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).resolve().parents[2] / "data" / "wstg" / "catalogue.json"
_SHORT = re.compile(r"^([A-Z]{4})-(\d\d)$")
_VERSIONED = re.compile(r"^WSTG-(?:v42|[0-9a-f]{7})-([A-Z]{4}-\d\d)$")


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Any]:
    return json.loads(PATH.read_text(encoding="utf-8"))


def tests() -> list[dict[str, Any]]:
    """Every test in the guide's order, each with its ``category`` and ``category_name``."""
    return [
        {**t, "category": c["id"], "category_name": c["name"]}
        for c in catalogue()["categories"]
        for t in c["tests"]
    ]


@lru_cache(maxsize=1)
def _by_short() -> dict[str, dict[str, Any]]:
    return {t["short"]: t for t in tests()}


def by_short(short: str) -> dict[str, Any] | None:
    return _by_short().get(str(short).upper())


def short_of(identifier: str) -> str | None:
    """``CONF-12`` from ``WSTG-87cf4a4-CONF-12``, ``WSTG-CONF-12`` or ``CONF-12``."""
    text = str(identifier or "").strip().upper()
    match = _VERSIONED.match(str(identifier or "").strip())
    if match:
        return match.group(1)
    text = text.removeprefix("WSTG-")
    return text if _SHORT.match(text) else None


def versioned(identifier: str) -> str | None:
    """The catalogue's versioned id for any spelling of a test's id; None when unknown."""
    short = short_of(identifier)
    test = by_short(short) if short else None
    return test["id"] if test else None


def commit() -> str:
    return str(catalogue()["commit"])


def attribution() -> str:
    return str(catalogue()["attribution"])
