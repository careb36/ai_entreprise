"""The ITIL transition set of a pack (issue #696).

What each release changed and left behind, compiled from the release
context's records (``collect_transition``) and the figures ``transition``
reads from them: a change record per release, release notes, the known
errors at release time, then a person's section for the handover. The
structure follows the practices ITIL 4 (AXELOS / PeopleCert) names for
moving a change into use (change enablement, release management, problem
management and its known errors, and the handover to the people who run the
result), in our own words and in the pack's language (``texts``,
``texts_transition_*``); no text of ITIL is reproduced. The set is placed in
the PMBOK Guide's Governance domain, Executing focus area, beside the issue
log.

Every field cites its record with a digest the backend computed or observed:
the integration (the chain hash of its audit row), each task as the
integration recorded it, the review (its audit row's chain hash, with the
``diff_sha256`` the backend computed from the daemon's diff), the suite (the
digest of the daemon's test run as kept), the publication and the local
merge (their audit rows), an undo (its audit row), the Verifier's reading
(the run's artifact digest) and the task event (its chain hash). Who approved
or merged is a company role. A review's words are a claim and are not read;
nor is a retrospective or an agent message. The handover is a person's
section (``statements``): a template, or a person's statement quoted as a
claim, below the records and never read by them.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import statements, texts, transition
from services.planning.project_docs.collect_transition import KEY
from services.planning.project_docs.control_docs import cell, date, event_ref
from services.planning.project_docs.layout import (
    build,
    missing_line,
    pmbok_note,
    tr_of,
)
from services.planning.project_docs.log_docs import verification_ref
from services.planning.project_docs.refs import Sources, ref
from services.planning.project_docs.sanitize import md_id, md_inline

HANDOVER = "transition.handover"
INPUTS = (KEY, statements.key_of("transition"))
MAX_LISTED = 20


def _short(value: Any) -> str:
    return f"`{md_id(str(value)[:12])}`" if value else "-"


def _known(tr, prefix: str, value: Any) -> str:
    key = f"{prefix}.{value}"
    return tr(key) if key in texts.TEXTS["en"] else cell(value, 40)


def _by(tr, code: Any) -> str:
    return _known(tr, "transition.by", code)


def integration_ref(release: dict[str, Any], tr) -> dict[str, Any]:
    return ref(
        "integration",
        release.get("integration_id"),
        sha256=release.get("integration_chain_hash") or release.get("sha256"),
        label=tr("transition.label_sprint", sprint=md_id(release.get("sprint_id"))),
    )


def _task_ref(task: dict[str, Any], release: dict[str, Any], tr) -> dict[str, Any]:
    return ref(
        "task",
        task.get("task_id"),
        sha256=task.get("sha256"),
        label=tr(
            "transition.label_integration", id=md_id(release.get("integration_id"))
        ),
    )


def _part_ref(
    kind: str, ident: Any, part: dict[str, Any], label: str
) -> dict[str, Any]:
    return ref(
        kind, ident, sha256=part.get("chain_hash") or part.get("sha256"), label=label
    )


def _tasks_line(tasks: list[dict[str, Any]]) -> str:
    ids = [f"T-{md_id(t.get('task_id'))}" for t in tasks[:MAX_LISTED]]
    more = f" (+{len(tasks) - MAX_LISTED})" if len(tasks) > MAX_LISTED else ""
    return ", ".join(ids) + more


def _changed(release, src, lang, tr) -> tuple[str, str]:
    shipped = transition.delivered(release)
    reqs = ", ".join(md_id(r) for r in transition.requirements(release)) or tr(
        "dash_none"
    )
    text = tr(
        "transition.changed_value",
        count=len(shipped),
        tasks=_tasks_line(shipped) or "-",
        requirements=reqs,
        base=cell(release.get("base"), 60),
        commit=_short(release.get("commit")),
    )
    refs = [integration_ref(release, tr)] + [_task_ref(t, release, tr) for t in shipped]
    return text, src.cite(*refs[: MAX_LISTED + 1])


def _approved(release, src, lang, tr) -> tuple[str, str]:
    review = release.get("review")
    if not review:
        return tr("transition.no_review"), "-"
    mark = src.cite(_review_ref(review, tr))
    key = {
        "approve": "transition.approved_value",
        "request_changes": "transition.changes_value",
    }.get(str(review.get("event")), "transition.no_decision_value")
    return tr(
        key, by=_by(tr, review.get("by")), date=date(review.get("recorded_at"))
    ), mark


def _review_ref(review: dict[str, Any], tr) -> dict[str, Any]:
    return _part_ref(
        "review",
        review.get("id"),
        review,
        tr(
            "transition.label_review",
            event=_known(tr, "transition.event", review.get("event")),
            by=_by(tr, review.get("by")),
        ),
    )


def _review(release, src, lang, tr) -> tuple[str, str]:
    review = release.get("review")
    if not review:
        return tr("transition.review_none"), "-"
    on = (
        tr("transition.on_commit")
        if review.get("on_commit")
        else tr("transition.on_other_commit", commit=_short(review.get("commit")))
    )
    text = tr(
        "transition.review_value",
        event=_known(tr, "transition.event", review.get("event")),
        outcome=texts.status(lang, review.get("outcome")),
        diff=_short(review.get("diff_sha256")),
        files=review.get("files") or 0,
        on=on,
        sod=cell(review.get("sod"), 40),
        count=release.get("reviews") or 1,
    )
    return text, src.cite(_review_ref(review, tr))


def _suite(release, src, lang, tr) -> tuple[str, str]:
    suite = release.get("suite") or {}
    if not suite:
        return tr("transition.suite_none"), "-"
    if suite.get("timed_out"):
        run = tr("transition.suite_timed_out")
    elif suite.get("exit_code") is not None:
        run = tr(
            "transition.suite_run",
            command=f"`{md_inline(suite.get('command'), 120)}`"
            if suite.get("command")
            else "-",
            exit=suite.get("exit_code"),
            failing="?" if suite.get("failing") is None else suite.get("failing"),
        )
    else:
        run = tr("transition.suite_not_run")
    text = tr(
        "transition.suite_value",
        status=texts.status(lang, suite.get("status")),
        run=run,
        commit=_short(suite.get("tested_commit")),
    )
    label = tr("transition.label_integration", id=md_id(release.get("integration_id")))
    return text, src.cite(
        _part_ref("suite", release.get("integration_id"), suite, label)
    )


def _published(release, src, lang, tr) -> tuple[str, str]:
    parts, refs = [], []
    for pub in release.get("publications") or []:
        how = _known(tr, "release.how", pub.get("how"))
        parts.append(
            tr(
                "transition.published_value",
                how=how,
                base=cell(pub.get("base"), 60),
                commit=_short(pub.get("commit")),
                date=date(pub.get("at")),
            )
        )
        refs.append(_part_ref("release", release.get("integration_id"), pub, how))
    merge = release.get("merge")
    if merge:
        way = (
            "already"
            if merge.get("already")
            else "fast_forward"
            if merge.get("fast_forward")
            else "merge_commit"
        )
        parts.append(
            tr(
                "transition.merged_value",
                base=cell(merge.get("base"), 60),
                way=tr(f"transition.merge.{way}"),
                commit=_short(merge.get("after") or merge.get("commit")),
                by=_by(tr, merge.get("by")),
                date=date(merge.get("at")),
            )
        )
        label = tr("transition.label_merge", base=md_inline(merge.get("base"), 60))
        refs.append(_part_ref("merge", release.get("integration_id"), merge, label))
    return "; ".join(parts) or "-", src.cite(*refs) if refs else "-"


def _undone(release, src, lang, tr) -> tuple[str, str]:
    found = release.get("undone") or []
    if not found:
        return tr("transition.undone_none"), "-"
    text = "; ".join(
        tr(
            "transition.undone_value",
            task=md_id(u.get("task_id")),
            run=md_id(u.get("run_id")),
            date=date(u.get("at")),
        )
        for u in found[:MAX_LISTED]
    )
    refs = [
        _part_ref(
            "undo",
            u.get("audit_log_id"),
            u,
            tr("transition.label_run", run=md_id(u.get("run_id"))),
        )
        for u in found[:MAX_LISTED]
    ]
    return text, src.cite(*refs)


def _heading(release, tr) -> str:
    return tr(
        "transition.release_heading",
        sprint=md_id(release.get("sprint_id")),
        id=md_id(release.get("integration_id")),
    )


def _change_records(found, src, lang, tr) -> list[str]:
    body = [tr("transition.changes_heading"), ""]
    if not found:
        return body + [missing_line(lang, tr("transition.no_releases"))]
    body += [tr("transition.changes_note")]
    rows = (
        ("what", _changed),
        ("approved", _approved),
        ("review", _review),
        ("suite", _suite),
        ("published", _published),
        ("undone", _undone),
    )
    for release in found:
        body += [
            "",
            _heading(release, tr),
            "",
            tr("transition.record_header"),
            "|---|---|---|",
        ]
        for key, fn in rows:
            value, marks = fn(release, src, lang, tr)
            body.append(f"| {tr('transition.field.' + key)} | {value} | {marks} |")
    return body


def _release_notes(found, src, lang, tr) -> list[str]:
    body = [tr("transition.notes_heading"), ""]
    if not found:
        return body + [missing_line(lang, tr("transition.no_releases"))]
    body += [tr("transition.notes_note")]
    for release in found:
        reqs = ", ".join(md_id(r) for r in transition.requirements(release))
        body += [
            "",
            _heading(release, tr),
            "",
            tr(
                "transition.notes_summary",
                date=date(release.get("released_at")),
                requirements=reqs or tr("dash_none"),
                mark=src.cite(integration_ref(release, tr)),
            ),
            "",
            tr("transition.notes_header"),
            "|---|---|---|---|---|",
        ]
        tasks = [*transition.delivered(release), *transition.left_out(release)]
        for task in tasks[: MAX_LISTED * 5]:
            covers = ", ".join(md_id(c) for c in task.get("covers") or []) or "-"
            if task.get("state") in transition.INTEGRATED:
                commit = _short(task.get("commit"))
            else:
                commit = tr(
                    "transition.left_out",
                    state=_known(tr, "transition.state", task.get("state")),
                )
            body.append(
                f"| T-{md_id(task.get('task_id'))} | {cell(task.get('title'), 80)} | "
                f"{covers} | {commit} | {src.cite(_task_ref(task, release, tr))} |"
            )
    return body


def _known_errors(found, src, lang, tr) -> list[str]:
    body = [tr("transition.errors_heading"), ""]
    if not found:
        return body + [missing_line(lang, tr("transition.no_releases"))]
    body += [tr("transition.errors_note"), ""]
    rows = []
    for release in found:
        sprint = md_id(release.get("sprint_id"))
        for error in transition.known_errors(release):
            if error["kind"] == transition.READING:
                item = error["reading"]
                what = tr(
                    f"transition.error.{error['problem']}",
                    verdict=texts.status(lang, item.get("verdict")),
                )
                since, mark = item.get("observed_at"), verification_ref(item, lang)
            else:
                item = error["event"]
                what = tr(
                    "transition.error.hold",
                    kind=texts.status(lang, item.get("kind")),
                    status=texts.status(lang, item.get("task_status_then") or "?"),
                )
                since, mark = item.get("created_at"), event_ref(item, lang)
            rows.append(
                f"| {sprint} | T-{md_id(item.get('task_id'))} | {what} | "
                f"{date(since)} | {src.cite(mark)} |"
            )
    if not rows:
        return body + [tr("transition.errors_none")]
    return body + [tr("transition.errors_header"), "|---|---|---|---|---|", *rows]


def document(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The ITIL transition set from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    found = transition.releases(snapshot)
    if not found:
        missing.append(tr("transition.section_releases"))
    body = [tr("transition.intro"), ""]
    body += _change_records(found, src, lang, tr)
    body += [""] + _release_notes(found, src, lang, tr)
    body += [""] + _known_errors(found, src, lang, tr)
    heading = tr(statements.SECTIONS[HANDOVER][1])
    body += ["", f"## {heading}", "", tr("transition.handover_note"), ""]
    body += statements.lines(snapshot, HANDOVER, src)
    body += ["", tr("transition.read_heading"), "", tr("transition.read")]
    return build(
        "transition",
        f"{tr('transition.framework')} {pmbok_note('transition', lang)}",
        body,
        src,
        snapshot,
        INPUTS,
        missing,
        persons=[heading],
        statements=statements.filled(snapshot, "transition"),
    )
