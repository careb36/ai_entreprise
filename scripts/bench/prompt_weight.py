"""The weight of the prompts the factory sent, per section (schema v3).

Every task-worker run's evidence carries ``prompt_sections`` (issue #443,
``backend/app/services/loop/worker/prompt_budget.py``): the backend's own
measure of the Developer prompt it sent, per named section, with what the
budget cut. ``readers.prompt_sections`` takes each task's latest run's measure
from the stored verification evidence; ``weigh`` sums them into the card's
``prompt`` object; ``merge`` sums the specs' objects into the totals.

These are estimated tokens (characters / 4, rounded up, as the backend
counts them), the backend's observation of what it sent, never the tokens a
provider reported (``tokens`` on the card). A run with no measure (a backend
from before #443, or a delivery not made by the task worker's prompt) is
counted apart (``prompt_sections_missing``), never as a prompt of zero tokens.

``render_lines`` writes the card's Markdown; ``render_table`` is the
before → after table ``compare.py`` shows and never judges.
"""

from typing import Any

RELATED_CODE = "related_code"
# The project wiki (#445) and the vector memory it is the first choice over:
# how many prompts carried each, to compare a run with the wiki on and off.
WIKI, MEMORY = "wiki", "memory"
OPTIONAL_COUNTS = ("wiki_prompts", "memory_prompts")


def empty() -> dict[str, Any]:
    """The ``prompt`` object of a spec with no run read."""
    return {
        "prompts": 0,
        "prompt_sections_missing": 0,
        "tokens_estimated": 0,
        "tokens_mean": 0.0,
        "system_tokens_estimated": 0,
        "sections": {},
        "cut_prompts": 0,
        "cut": {},
        "related_code_prompts": 0,
        "wiki_prompts": 0,
        "memory_prompts": 0,
    }


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _measure(value: Any) -> dict[str, Any] | None:
    """*value* when it reads as a ``prompt_budget.fit`` measure, else None."""
    if not isinstance(value, dict) or not _count(value.get("tokens")):
        return None
    sections = value.get("sections")
    if not isinstance(sections, list) or not all(
        isinstance(s, dict)
        and isinstance(s.get("name"), str)
        and _count(s.get("tokens"))
        for s in sections
    ):
        return None
    return value


def _finish(out: dict[str, Any]) -> dict[str, Any]:
    """The mean and each section's share, from the sums."""
    out["tokens_mean"] = (
        round(out["tokens_estimated"] / out["prompts"], 1) if out["prompts"] else 0.0
    )
    sent = sum(s["tokens"] for s in out["sections"].values())
    for section in out["sections"].values():
        section["share"] = round(section["tokens"] / sent, 4) if sent else 0.0
    out["sections"] = dict(sorted(out["sections"].items()))
    out["cut"] = dict(sorted(out["cut"].items()))
    return out


def weigh(measures: list[Any]) -> dict[str, Any]:
    """One spec's ``prompt`` object from its tasks' latest measures (None, or
    anything that is not a measure, counts as missing).

    A section's weight is the tokens it took in the prompts that sent it; a
    section the budget cut is counted under ``cut``, not in the weight."""
    out = empty()
    for raw in measures:
        measure = _measure(raw)
        if measure is None:
            out["prompt_sections_missing"] += 1
            continue
        out["prompts"] += 1
        out["tokens_estimated"] += measure["tokens"]
        system = measure.get("system_tokens")
        out["system_tokens_estimated"] += system if _count(system) else 0
        cut = [s["name"] for s in measure["sections"] if s.get("cut")]
        for section in measure["sections"]:
            if section.get("cut"):
                continue
            row = out["sections"].setdefault(
                section["name"], {"tokens": 0, "prompts": 0}
            )
            row["tokens"] += section["tokens"]
            row["prompts"] += 1
        if cut:
            out["cut_prompts"] += 1
            for name in cut:
                out["cut"][name] = out["cut"].get(name, 0) + 1
        if any(
            s["name"] == RELATED_CODE and not s.get("cut") and s["tokens"]
            for s in measure["sections"]
        ):
            out["related_code_prompts"] += 1
        for name, key in ((WIKI, "wiki_prompts"), (MEMORY, "memory_prompts")):
            if any(
                s["name"] == name and not s.get("cut") and s["tokens"]
                for s in measure["sections"]
            ):
                out[key] += 1
    return _finish(out)


def merge(objects: list[dict[str, Any] | None]) -> dict[str, Any]:
    """The totals' ``prompt`` object: the specs' sums, mean and shares recomputed."""
    out = empty()
    for obj in objects:
        if not obj:
            continue
        for key in (
            "prompts",
            "prompt_sections_missing",
            "tokens_estimated",
            "system_tokens_estimated",
            "cut_prompts",
            "related_code_prompts",
        ):
            out[key] += obj[key]
        for key in OPTIONAL_COUNTS:  # absent on a card before #445
            out[key] += obj.get(key) or 0
        for name, section in obj["sections"].items():
            row = out["sections"].setdefault(name, {"tokens": 0, "prompts": 0})
            row["tokens"] += section["tokens"]
            row["prompts"] += section["prompts"]
        for name, times in obj["cut"].items():
            out["cut"][name] = out["cut"].get(name, 0) + times
    return _finish(out)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def summary(obj: dict[str, Any] | None) -> str:
    """One line: the prompts measured, their mean, what was cut, related code."""
    if not obj:
        return "not recorded (a card before schema v3)"
    cut = (
        ", ".join(f"{name} ×{times}" for name, times in obj["cut"].items())
        if obj["cut"]
        else "none"
    )
    line = (
        f"{obj['prompts']} prompts, {obj['tokens_mean']:.0f} estimated tokens per task "
        f"({obj['tokens_estimated']} in all, system prompt {obj['system_tokens_estimated']} apart); "
        f"{obj['cut_prompts']} with sections cut ({cut}); "
        f"{obj['related_code_prompts']} with related code; "
        f"{obj.get('wiki_prompts', 0)} with the wiki, "
        f"{obj.get('memory_prompts', 0)} with vector memory"
    )
    if obj["prompt_sections_missing"]:
        line += (
            f"; {obj['prompt_sections_missing']} runs without a measure, counted apart"
        )
    return line


def render_lines(card: dict[str, Any]) -> list[str]:
    """The card's prompt-weight section: the totals' sections by weight."""
    total = (card.get("totals") or {}).get("prompt")
    if total is None:
        return []
    lines = [
        "",
        "## Prompt weight",
        "",
        "Estimated tokens (characters / 4) of the Developer prompts the backend sent,",
        "its own measure per section, apart from the tokens the provider reported.",
        "",
        f"- Total: {summary(total)}",
    ]
    lines += [f"- {s['name']}: {summary(s.get('prompt'))}" for s in card["specs"]]
    if total["sections"]:
        lines += [
            "",
            "| Section | Estimated tokens | Share | Prompts |",
            "|---|---|---|---|",
        ]
        for name, row in sorted(
            total["sections"].items(), key=lambda kv: (-kv[1]["tokens"], kv[0])
        ):
            lines.append(
                f"| {name} | {row['tokens']} | {_pct(row['share'])} | {row['prompts']} |"
            )
    return lines


def render_table(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """The sections' weight before → after (shown, never judged); empty when
    neither card measured a prompt."""
    before = (previous.get("totals") or {}).get("prompt") or empty()
    after = (current.get("totals") or {}).get("prompt") or empty()
    if not before["prompts"] and not after["prompts"]:
        return []

    def cell(obj: dict[str, Any], name: str) -> tuple[str, str]:
        row = obj["sections"].get(name)
        return (str(row["tokens"]), _pct(row["share"])) if row else ("–", "–")

    def mean(obj: dict[str, Any]) -> str:
        # Nothing measured (a card before v3) is shown as such, never as 0.
        return f"{obj['tokens_mean']:.0f}" if obj["prompts"] else "–"

    lines = [
        "",
        "### Prompt weight (estimated tokens, characters / 4)",
        "",
        (
            f"Mean per task {mean(before)} → {mean(after)}; "
            f"prompts with sections cut {before['cut_prompts']} → {after['cut_prompts']}; "
            f"with related code {before['related_code_prompts']} → {after['related_code_prompts']}; "
            f"with the wiki {before.get('wiki_prompts', 0)} → {after.get('wiki_prompts', 0)}; "
            f"with vector memory {before.get('memory_prompts', 0)} → {after.get('memory_prompts', 0)}; "
            f"runs without a measure {before['prompt_sections_missing']} → {after['prompt_sections_missing']}."
        ),
        "",
        "| Section | Estimated tokens | Share |",
        "|---|---|---|",
    ]
    for name in sorted(set(before["sections"]) | set(after["sections"])):
        (bt, bs), (at, as_) = cell(before, name), cell(after, name)
        lines.append(f"| {name} | {bt} → {at} | {bs} → {as_} |")
    return lines
