"""The browser sandbox image of the project's Playwright release (issue #315).

A browser build matches one Playwright release: a project on another version
looks for its own browser revision and fails, or behaves differently.
``images.yml`` builds the browser sandbox image for each release listed in
``vscode-daemon/images/browser/playwright_versions.json`` and prints the
digest of each; the operator lists them in ``DELIVERY_BROWSER_IMAGES``
(``1.63.0=ghcr.io/…@sha256:…,1.64.1=ghcr.io/…@sha256:…``). Each image is
named by its digest (or a local content ID, ``sha256:…``), never by a tag
that can be moved: an entry with a tag is refused, recorded, and never run.

``select`` takes the daemon's observation of the version the project pins
(``snapshot["playwright"]`` of ``workspace.inspect``,
``vscode-daemon/playwright_pin.py``), never the producer's report, and says:

- ``exact``: the listed image of that release;
- ``fallback``: a release not listed runs in ``DELIVERY_BROWSER_IMAGE`` (or
  the sandbox image when that is empty), with the mismatch recorded;
- ``not_declared``: the project declares no Playwright, so the image's own
  runs, in ``DELIVERY_BROWSER_IMAGE``;
- ``unknown``: nobody could tell the version (a range no lockfile resolves,
  no observation from the daemon, an observation not the daemon's): no image
  is known to match, so the loop does not run the browser suite and
  ``e2e_run`` is unverifiable with the reason, never a pass.

Configuration and observations only; ``services/evidence`` judges the run.
"""

from __future__ import annotations

import re
from typing import Any

SOURCE_LIST = "DELIVERY_BROWSER_IMAGES"
SOURCE_DEFAULT = "DELIVERY_BROWSER_IMAGE"
MATCH_EXACT = "exact"
MATCH_FALLBACK = "fallback"
MATCH_NOT_DECLARED = "not_declared"
MATCH_UNKNOWN = "unknown"

_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
_BY_DIGEST = re.compile(r"^(?:[^\s@=,]+@)?sha256:[0-9a-f]{64}$")
_PIN_FIELDS = (
    "declared",
    "package",
    "spec",
    "version",
    "from",
    "reason",
    "observed_by",
)


def pinned_by_digest(ref: str) -> bool:
    """Whether *ref* names an image by its content: ``repo@sha256:…`` or a
    local content ID ``sha256:…``."""
    return bool(_BY_DIGEST.match(ref or ""))


def parse(raw: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    """``DELIVERY_BROWSER_IMAGES`` as ``{release: image}`` and the entries
    refused, each with its reason."""
    images: dict[str, str] = {}
    refused: list[dict[str, str]] = []
    twice: set[str] = set()
    for entry in re.split(r"[\s,]+", (raw or "").strip()):
        if not entry:
            continue
        version, sep, ref = entry.partition("=")
        if not sep or not version or not ref:
            refused.append(
                {"entry": entry[:200], "reason": "not a version=image entry"}
            )
        elif not _VERSION.match(version):
            refused.append(
                {
                    "entry": entry[:200],
                    "reason": f"{version[:40]!r} is not an exact Playwright version",
                }
            )
        elif not pinned_by_digest(ref):
            refused.append(
                {
                    "entry": entry[:200],
                    "reason": "the image is not named by its digest (@sha256:…); a tag can be moved",
                }
            )
        elif version in images and images[version] != ref:
            twice.add(version)
        else:
            images[version] = ref
    for version in sorted(twice):
        del images[version]
        refused.append(
            {
                "entry": version,
                "reason": f"Playwright {version} is listed twice with two images",
            }
        )
    return images, refused


def _pin_record(pin: Any) -> dict[str, Any] | None:
    if not isinstance(pin, dict):
        return None
    return {
        k: (str(pin[k])[:200] if isinstance(pin[k], str) else pin[k])
        for k in _PIN_FIELDS
        if k in pin
    }


def select(
    pin: Any,
    default_image: str,
    images: dict[str, str],
    refused: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """The image for a browser e2e command, and why (the module's
    docstring); ``None`` when no release is listed (and none was refused):
    the check is as before."""
    if not images and not refused:
        return None
    default_image = (default_image or "").strip()
    record: dict[str, Any] = {
        "pin": _pin_record(pin),
        "available": sorted(images),
        "decided_by": "backend",
    }
    if refused:
        record["refused"] = refused
    if not isinstance(pin, dict):
        return {
            **record,
            "match": MATCH_UNKNOWN,
            "image": None,
            "reason": "the daemon's snapshot carries no Playwright observation "
            "(a daemon from before per-release browser images, or no snapshot)",
        }
    if pin.get("observed_by") != "daemon":
        return {
            **record,
            "match": MATCH_UNKNOWN,
            "image": None,
            "reason": "the Playwright version on record is not the daemon's observation",
        }
    if pin.get("declared") is False:
        return {
            **record,
            "match": MATCH_NOT_DECLARED,
            "image": default_image or None,
            "source": SOURCE_DEFAULT,
            "reason": f"{pin.get('reason') or 'the project declares no Playwright'}: "
            "the image's own Playwright runs",
        }
    version = pin.get("version")
    if not isinstance(version, str) or not _VERSION.match(version):
        if isinstance(version, str) and version:
            why = f"{version[:40]!r} is not an exact version"
        else:
            why = pin.get("reason") or "no version observed"
        return {
            **record,
            "match": MATCH_UNKNOWN,
            "image": None,
            "reason": f"the project's Playwright version is unknown: {why}",
        }
    record["wanted"] = version
    if version in images:
        return {
            **record,
            "match": MATCH_EXACT,
            "image": images[version],
            "source": SOURCE_LIST,
        }
    has = next((v for v, ref in images.items() if ref == default_image), None)
    return {
        **record,
        "match": MATCH_FALLBACK,
        "image": default_image or None,
        "source": SOURCE_DEFAULT,
        "mismatch": {
            "wanted": version,
            "image_playwright": has,
            "available": sorted(images),
        },
        "reason": f"no browser image is listed for Playwright {version}; "
        + ("the default image runs" if default_image else "the sandbox image runs"),
    }


def unverifiable_e2e(command: str, choice: dict[str, Any]) -> dict[str, Any]:
    """``app_run["e2e"]`` for a browser suite not run because no image is
    known to match the project's Playwright: ``runtime_missing`` makes
    ``e2e_run`` unverifiable with the reason."""
    return {
        "command": command,
        "ran": False,
        "decided_by": "backend",
        "runtime_missing": f"not run: {choice.get('reason') or 'no browser image matches'}",
    }
