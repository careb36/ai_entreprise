"""Identity-provider groups mapped to teams: the pure part (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``).

Everything here is a function of its arguments; nothing reads the database,
the clock or the network. ``read_claim`` classifies the configured claim of a
validated ID token (``present``, ``empty``, ``absent``, ``overage``,
``malformed``); ``evaluate`` turns that reading, the connection's mapping, the
teams it names, the person's memberships, exclusions and manual exceptions
into the memberships to add and remove and the skips to record. A refusal is
a recorded skip, never an error (G16). No model takes part (G18).

The rules (EARS, the design's R-numbers):

- an absent claim changes nothing (R7); an overage marker
  (``_claim_names`` naming the claim, or ``hasgroups``) or a claim that is not
  a list of at most 1000 strings of at most 256 characters changes nothing
  (R8); only a present, empty list removes (R9);
- only teams the connection maps, of its company and SSO-managed, are
  written (R5); a team above the cap gets nobody added (R6); an exclusion in
  force stops an add, a manual exception in force stops a removal (R11);
- a stale token changes nothing (R10), nor does a person who is not a member
  of the company (R4).

The Entra overage claim and ``hasgroups`` follow Microsoft's public
documentation of the ID token (behaviour, not code); the endpoint of
``_claim_sources`` is reduced to its host and never fetched (G4).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

ROLE_RANK = {"Viewer": 1, "Manager": 2, "Owner": 3}
CAPS = ("Viewer", "Manager")  # never "Owner" (G1)
SOURCES = ("owner", "sso")
DEFAULT_CLAIM = "groups"
ENTRA_CLAIM = "roles"  # app roles: no overage (R17, G4)
CLAIM_RE = re.compile(r"^[A-Za-z0-9_\-.:/]{1,64}$")
GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
MAX_VALUES = 1000
MAX_VALUE_CHARS = 256
MAX_PAIRS = 200
MAX_HOST_CHARS = 253

PRESENT, EMPTY, ABSENT, OVERAGE, MALFORMED = (
    "present",
    "empty",
    "absent",
    "overage",
    "malformed",
)
# Skip reasons for one team, and for the whole observation.
ROLE_ABOVE_CAP = "role_above_cap"
NOT_SSO_MANAGED = "not_sso_managed"
OTHER_COMPANY = "other_company"
EXCLUDED = "excluded"
MANUAL_EXCEPTION = "manual_exception"
STALE_TOKEN = "stale_token"
NOT_A_MEMBER = "not_a_member"
GROUPS_DISABLED = "groups_disabled"
# What makes an evaluation worth an audit row beyond a change (R13).
AUDITED_SKIPS = frozenset({STALE_TOKEN, ROLE_ABOVE_CAP, EXCLUDED})
AUDITED_STATES = frozenset({OVERAGE, MALFORMED})


class GroupConfigError(ValueError):
    """A configuration that cannot be saved; ``status`` and ``detail`` say why."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def sha256_of(values) -> str:
    """The backend's sha256 of a sorted, de-duplicated list of strings."""
    data = json.dumps(sorted(set(values)), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compares_lower(preset: str, claim: str) -> bool:
    """Entra group ids (GUIDs) are compared lower-cased; anything else exactly (R17)."""
    return preset == "entra" and claim == DEFAULT_CLAIM


def proposed_claim(preset: str) -> str:
    """The claim the page proposes for a preset: app roles for Entra (R17)."""
    return ENTRA_CLAIM if preset == "entra" else DEFAULT_CLAIM


def clean_claim(value: str | None) -> str:
    claim = (value or "").strip()
    if not CLAIM_RE.match(claim):
        raise GroupConfigError(
            422,
            "groups_claim: 1 to 64 letters, digits or the characters _ - . : /",
        )
    return claim


def clean_cap(value: str | None) -> str:
    if value not in CAPS:
        raise GroupConfigError(422, "group_role_cap: Viewer or Manager, never Owner")
    return value


def clean_value(raw: str, preset: str, claim: str) -> str:
    """A group value as stored: exact, or a lower-cased GUID for Entra groups."""
    if not isinstance(raw, str) or not (1 <= len(raw) <= MAX_VALUE_CHARS):
        raise GroupConfigError(422, f"group_value: 1 to {MAX_VALUE_CHARS} characters")
    if compares_lower(preset, claim):
        if not GUID_RE.match(raw):
            raise GroupConfigError(
                422,
                "group_value: an Entra group is named by its object id (a GUID); "
                "display names are not unique",
            )
        return raw.lower()
    return raw


def proposal_sha256(
    connection_id: int, group_value: str, team_id: int, team_role: str, cap: str
) -> str:
    """What a second Owner approves, hashed by the backend (G19)."""
    data = json.dumps(
        {
            "connection_id": connection_id,
            "group_value": group_value,
            "team_id": team_id,
            "team_role": team_role,
            "group_role_cap": cap,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def members_sha256(member_ids, role: str) -> str:
    """A team's member set and role, hashed for the conversion preview (G22)."""
    data = json.dumps(
        {"members": sorted(set(member_ids)), "role": role}, separators=(",", ":")
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClaimReading:
    state: str
    values: frozenset[str] = frozenset()
    count: int = 0
    sha256: str | None = None
    overage_host: str | None = None


def _host(endpoint) -> str | None:
    if not isinstance(endpoint, str):
        return None
    try:
        host = urlsplit(endpoint).hostname
    except ValueError:
        return None
    return host[:MAX_HOST_CHARS] if host else None


def read_claim(claims: dict, claim: str, lower: bool = False) -> ClaimReading:
    """Classify *claim* in validated ID token *claims* (R7–R9)."""
    names = claims.get("_claim_names")
    if (isinstance(names, dict) and claim in names) or claims.get("hasgroups") is True:
        host = None
        sources = claims.get("_claim_sources")
        if isinstance(names, dict) and isinstance(sources, dict):
            source = sources.get(names.get(claim))
            if isinstance(source, dict):
                host = _host(source.get("endpoint"))
        return ClaimReading(OVERAGE, overage_host=host)
    if claim not in claims:
        return ClaimReading(ABSENT)
    raw = claims[claim]
    if (
        not isinstance(raw, list)
        or len(raw) > MAX_VALUES
        or any(not isinstance(v, str) or len(v) > MAX_VALUE_CHARS for v in raw)
    ):
        return ClaimReading(MALFORMED)
    values = frozenset(v.lower() if lower else v for v in raw)
    return ClaimReading(
        EMPTY if not values else PRESENT,
        values=values,
        count=len(values),
        sha256=sha256_of(values),
    )


@dataclass(frozen=True)
class TeamInfo:
    id: int
    company_id: int
    name: str
    role: str
    membership_source: str


@dataclass
class Outcome:
    applied: bool = False
    add: list[int] = field(default_factory=list)
    remove: list[int] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    because: dict[int, list[str]] = field(default_factory=dict)

    def audited(self, state: str) -> bool:
        """Whether this evaluation needs an ``sso_group_sync`` row (R13)."""
        return bool(
            self.add
            or self.remove
            or state in AUDITED_STATES
            or any(s["reason"] in AUDITED_SKIPS for s in self.skipped)
        )


def stale(token_iat: datetime | None, last_applied_iat: datetime | None) -> bool:
    """A token issued before the last applied one for this person (R10)."""
    return (
        token_iat is not None
        and last_applied_iat is not None
        and token_iat < last_applied_iat
    )


def evaluate(
    reading: ClaimReading,
    *,
    company_id: int,
    cap: str,
    mappings: list[tuple[str, int]],
    teams: dict[int, TeamInfo],
    current: set[int],
    excluded: set[int],
    exceptions: set[int],
    is_member: bool = True,
    is_stale: bool = False,
    enabled: bool = True,
) -> Outcome:
    """What the mapping does with *reading* (R4–R11); pure.

    *mappings* are the connection's active pairs (a pending approval is not
    one); *teams* the teams they name; *current* the person's teams;
    *excluded* and *exceptions* the teams where an exclusion or a manual
    exception of theirs is in force.
    """
    out = Outcome()
    mapped: dict[int, set[str]] = {}
    for value, team_id in mappings:
        mapped.setdefault(team_id, set()).add(value)
    out.matched = sorted(
        reading.values & {v for values in mapped.values() for v in values}
    )
    if not enabled:
        out.skipped.append({"team_id": None, "reason": GROUPS_DISABLED})
        return out
    if not is_member:
        out.skipped.append({"team_id": None, "reason": NOT_A_MEMBER})
        return out
    if reading.state not in (PRESENT, EMPTY):
        return out
    if is_stale:
        out.skipped.append({"team_id": None, "reason": STALE_TOKEN})
        return out
    out.applied = True
    cap_rank = ROLE_RANK.get(cap, ROLE_RANK["Viewer"])
    for team_id in sorted(mapped):
        team = teams.get(team_id)
        if team is None:
            continue
        if team.company_id != company_id:
            out.skipped.append({"team_id": team_id, "reason": OTHER_COMPANY})
            continue
        if team.membership_source != "sso":
            out.skipped.append({"team_id": team_id, "reason": NOT_SSO_MANAGED})
            continue
        hits = sorted(reading.values & mapped[team_id])
        if hits and team_id not in current:
            if team_id in excluded:
                out.skipped.append({"team_id": team_id, "reason": EXCLUDED})
            elif ROLE_RANK.get(team.role, 99) > cap_rank:
                out.skipped.append({"team_id": team_id, "reason": ROLE_ABOVE_CAP})
            else:
                out.add.append(team_id)
                out.because[team_id] = hits
        elif not hits and team_id in current:
            if team_id in exceptions:
                out.skipped.append({"team_id": team_id, "reason": MANUAL_EXCEPTION})
            else:
                out.remove.append(team_id)
                out.because[team_id] = []
    return out


def justified_only_by(
    matched: list[str], removed_value: str, still_mapped: set[str]
) -> bool:
    """Whether a membership rested on *removed_value* alone: the person's last
    applied observation matched it and no value still mapped to the team (R20)."""
    seen = set(matched or [])
    return removed_value in seen and not (seen & still_mapped)
