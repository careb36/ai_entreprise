"""The pure evaluation of the SSO group mapping (issue #703;
``docs/design/SSO_GROUP_MAPPING.md``, R5–R11, R17, G4, G9, G15): one case per
claim state, Entra's overage marker with ``_claim_names``/``_claim_sources``
and with ``hasgroups``, an empty list, a malformed or oversized claim, the
cap, another company's team, an Owner-managed team, a stale token, an
exclusion never re-added, a manual exception never removed, a pending
approval (not a mapping) never applied."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from services.platform import sso_groups as rules

COMPANY = 1


def _teams(**overrides):
    teams = {
        10: rules.TeamInfo(10, COMPANY, "Platform", "Viewer", "sso"),
        11: rules.TeamInfo(11, COMPANY, "Leads", "Manager", "sso"),
        12: rules.TeamInfo(12, COMPANY, "Hand-picked", "Viewer", "owner"),
        13: rules.TeamInfo(13, 2, "Elsewhere", "Viewer", "sso"),
    }
    teams.update(overrides)
    return teams


def _run(reading, mappings, current=(), cap="Viewer", **kwargs):
    return rules.evaluate(
        reading,
        company_id=COMPANY,
        cap=cap,
        mappings=mappings,
        teams=_teams(),
        current=set(current),
        excluded=set(kwargs.pop("excluded", ())),
        exceptions=set(kwargs.pop("exceptions", ())),
        **kwargs,
    )


# -- Reading the claim (R7–R9, G4, G9) -----------------------------------------


def test_each_claim_state_is_recognised():
    present = rules.read_claim({"groups": ["a", "b", "a"]}, "groups")
    assert present.state == "present" and present.values == {"a", "b"}
    assert present.count == 2 and present.sha256 == rules.sha256_of(["b", "a"])
    empty = rules.read_claim({"groups": []}, "groups")
    assert (empty.state, empty.count) == ("empty", 0) and empty.sha256
    assert rules.read_claim({"email": "x"}, "groups").state == "absent"
    # A namespaced claim is a key like any other.
    url_claim = "https://claims.example/groups"
    assert rules.read_claim({url_claim: ["a"]}, url_claim).state == "present"


def test_entra_overage_is_recognised_and_its_endpoint_kept_as_a_host_only():
    claims = {
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {
            "src1": {
                "endpoint": "https://graph.windows.net/tenant/users/oid/getMemberObjects"
            }
        },
    }
    reading = rules.read_claim(claims, "groups")
    assert reading.state == "overage" and reading.overage_host == "graph.windows.net"
    assert reading.values == frozenset() and reading.sha256 is None
    marked = rules.read_claim({"hasgroups": True}, "groups")
    assert marked.state == "overage" and marked.overage_host is None
    # An overage of another claim leaves this one to be read.
    other = rules.read_claim({"_claim_names": {"groups": "s"}, "roles": ["r"]}, "roles")
    assert other.state == "present"


@pytest.mark.parametrize(
    "value",
    [
        "eng",
        {"eng": True},
        ["eng", 3],
        ["eng", ["nested"]],
        ["x"] * (rules.MAX_VALUES + 1),
        ["y" * (rules.MAX_VALUE_CHARS + 1)],
        None,
    ],
)
def test_anything_but_a_bounded_list_of_strings_is_malformed(value):
    assert rules.read_claim({"groups": value}, "groups").state == "malformed"


def test_entra_group_ids_compare_lower_cased_and_app_roles_exactly():
    upper = "6F1C5D3A-0B2E-4C7D-9A8B-1E2F3A4B5C6D"
    assert rules.read_claim({"groups": [upper]}, "groups", lower=True).values == {
        upper.lower()
    }
    assert rules.read_claim({"roles": ["Ops.Admin"]}, "roles").values == {"Ops.Admin"}
    assert rules.compares_lower("entra", "groups")
    assert not rules.compares_lower("entra", "roles")
    assert not rules.compares_lower("generic", "groups")
    assert rules.proposed_claim("entra") == "roles"
    assert rules.proposed_claim("generic") == "groups"


# -- Evaluating it (R5, R6, R10, R11) ----------------------------------------


def test_a_present_claim_adds_the_mapped_teams_and_removes_the_others():
    reading = rules.read_claim({"groups": ["eng", "unmapped"]}, "groups")
    out = _run(reading, [("eng", 10), ("ops", 10), ("lead", 11)], current={10, 11})
    assert out.applied and out.add == [] and out.remove == [11]
    out = _run(reading, [("eng", 10), ("lead", 11)], current=set())
    assert out.add == [10] and out.because[10] == ["eng"]
    assert out.matched == ["eng"]  # never the unmapped value


def test_a_team_reached_by_several_values_counts_once_and_a_member_is_left_alone():
    reading = rules.read_claim({"groups": ["eng", "ops"]}, "groups")
    out = _run(reading, [("eng", 10), ("ops", 10)])
    assert out.add == [10] and out.because[10] == ["eng", "ops"]
    unchanged = _run(reading, [("eng", 10), ("ops", 10)], current={10})
    assert unchanged.applied and not unchanged.add and not unchanged.remove
    assert not unchanged.audited("present")


def test_an_empty_claim_removes_and_an_absent_overage_or_malformed_one_changes_nothing():
    mappings = [("eng", 10)]
    empty = _run(rules.read_claim({"groups": []}, "groups"), mappings, current={10})
    assert empty.applied and empty.remove == [10]
    for claims in (
        {},
        {"hasgroups": True},
        {"_claim_names": {"groups": "s"}},
        {"groups": "eng"},
    ):
        out = _run(rules.read_claim(claims, "groups"), mappings, current={10})
        assert not out.applied and out.add == out.remove == []
    assert _run(rules.read_claim({"hasgroups": True}, "groups"), mappings).audited(
        "overage"
    )
    assert not _run(rules.read_claim({}, "groups"), mappings).audited("absent")


def test_only_sso_managed_teams_of_the_company_are_written():
    reading = rules.read_claim({"groups": ["a", "b"]}, "groups")
    out = _run(reading, [("a", 12), ("b", 13)], current={12, 13})
    assert out.add == [] and out.remove == []
    assert out.skipped == [
        {"team_id": 12, "reason": "not_sso_managed"},
        {"team_id": 13, "reason": "other_company"},
    ]


def test_a_team_above_the_cap_gets_nobody_added_but_removals_still_apply():
    reading = rules.read_claim({"groups": ["lead"]}, "groups")
    out = _run(reading, [("lead", 11)])
    assert out.add == [] and out.skipped == [
        {"team_id": 11, "reason": "role_above_cap"}
    ]
    assert out.audited("present")
    assert _run(reading, [("lead", 11)], cap="Manager").add == [11]
    gone = _run(
        rules.read_claim({"groups": []}, "groups"), [("lead", 11)], current={11}
    )
    assert gone.remove == [11]


def test_an_exclusion_is_never_re_added_and_a_manual_exception_never_removed():
    reading = rules.read_claim({"groups": ["eng"]}, "groups")
    out = _run(reading, [("eng", 10)], excluded={10})
    assert out.add == [] and out.skipped == [{"team_id": 10, "reason": "excluded"}]
    for claims in ({"groups": []}, {"groups": ["other"]}):
        kept = _run(
            rules.read_claim(claims, "groups"),
            [("eng", 10)],
            current={10},
            exceptions={10},
        )
        assert kept.remove == []
        assert kept.skipped == [{"team_id": 10, "reason": "manual_exception"}]


def test_a_stale_token_or_a_non_member_changes_nothing():
    t0 = datetime(2026, 11, 19, 12, 0, 0)
    assert rules.stale(t0, t0 + timedelta(seconds=1))
    assert not rules.stale(t0, t0) and not rules.stale(None, t0)
    assert not rules.stale(t0, None)
    reading = rules.read_claim({"groups": []}, "groups")
    stale = _run(reading, [("eng", 10)], current={10}, is_stale=True)
    assert not stale.applied and stale.remove == []
    assert stale.skipped == [{"team_id": None, "reason": "stale_token"}]
    assert stale.audited("empty")
    stranger = _run(reading, [("eng", 10)], current={10}, is_member=False)
    assert stranger.skipped == [{"team_id": None, "reason": "not_a_member"}]
    off = _run(reading, [("eng", 10)], current={10}, enabled=False)
    assert off.skipped == [{"team_id": None, "reason": "groups_disabled"}]


def test_a_pending_approval_is_not_a_mapping():
    """Only active pairs are passed in: a Manager team with nothing mapped is
    never touched, whatever the claim says."""
    reading = rules.read_claim({"groups": ["lead"]}, "groups")
    out = _run(reading, [], current={11}, cap="Manager")
    assert out.add == out.remove == [] and out.applied


# -- Configuration values and hashes (R16, R17, G15, G19, G22) ---------------


def test_the_values_the_owner_writes_are_checked():
    guid = "6F1C5D3A-0B2E-4C7D-9A8B-1E2F3A4B5C6D"
    assert rules.clean_value(guid, "entra", "groups") == guid.lower()
    with pytest.raises(rules.GroupConfigError):
        rules.clean_value("All Engineers", "entra", "groups")
    assert rules.clean_value("Ops.Admin", "entra", "roles") == "Ops.Admin"
    assert rules.clean_value("Eng ", "generic", "groups") == "Eng "
    for bad in ("", "x" * (rules.MAX_VALUE_CHARS + 1)):
        with pytest.raises(rules.GroupConfigError):
            rules.clean_value(bad, "generic", "groups")
    assert rules.clean_claim("https://claims.example/groups")
    for bad in ("", "a b", "x" * 65, "groups;drop"):
        with pytest.raises(rules.GroupConfigError):
            rules.clean_claim(bad)
    with pytest.raises(rules.GroupConfigError):
        rules.clean_cap("Owner")


def test_the_hashes_bind_what_was_proposed_and_what_was_previewed():
    base = rules.proposal_sha256(1, "lead", 11, "Manager", "Manager")
    assert base == rules.proposal_sha256(1, "lead", 11, "Manager", "Manager")
    assert base != rules.proposal_sha256(1, "lead", 11, "Viewer", "Manager")
    assert base != rules.proposal_sha256(1, "lead", 11, "Manager", "Viewer")
    assert base != rules.proposal_sha256(1, "lead2", 11, "Manager", "Manager")
    assert rules.members_sha256([2, 1], "Viewer") == rules.members_sha256(
        [1, 2], "Viewer"
    )
    assert rules.members_sha256([1], "Viewer") != rules.members_sha256([1, 3], "Viewer")
    assert rules.members_sha256([1], "Viewer") != rules.members_sha256([1], "Manager")


def test_a_removed_pair_justified_a_membership_only_when_nothing_else_matches():
    assert rules.justified_only_by(["eng"], "eng", set())
    assert not rules.justified_only_by(["eng", "ops"], "eng", {"ops"})
    assert not rules.justified_only_by(["ops"], "eng", set())
    assert not rules.justified_only_by([], "eng", set())
