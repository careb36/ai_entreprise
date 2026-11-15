"""Coloured terminal output and the result accumulator every suite writes to."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, List

# ── ANSI colour helpers (no extra deps) ──────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
BLUE   = "\033[94m"

def colour(text: str, *codes: str) -> str:
    return "".join(codes) + str(text) + RESET

def ok(msg: str)   -> str: return colour(f"  ✓  {msg}", GREEN, BOLD)
def fail(msg: str) -> str: return colour(f"  ✗  {msg}", RED,   BOLD)
def warn(msg: str) -> str: return colour(f"  ⚠  {msg}", YELLOW)
def info(msg: str) -> str: return colour(f"     {msg}", DIM)
def head(msg: str) -> str: return colour(f"\n{'─'*60}\n  {msg}\n{'─'*60}", CYAN, BOLD)
def hint(var: str, desc: str) -> None:
    print(colour(f"     → Set {var} in your .env  ({desc})", YELLOW))

def note(msg: str) -> None:
    print(colour(f"  ℹ  {msg}", BLUE))


# ── Result accumulator ────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    suite: str
    name: str
    passed: bool
    detail: str = ""
    elapsed_ms: float = 0.0

@dataclass
class Suite:
    name: str
    results: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", elapsed_ms: float = 0.0) -> None:
        self.results.append(CheckResult(self.name, name, passed, detail, elapsed_ms))
        icon = ok(name) if passed else fail(name)
        print(icon)
        if detail:
            for line in detail.splitlines():
                print(info(line))
        if elapsed_ms:
            print(info(f"({elapsed_ms:.0f} ms)"))

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)


def timed(fn: Callable[[], Any]):
    """Run fn(), return (result, elapsed_ms)."""
    t0 = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - t0) * 1000
