"""The bench's committed scorecards, for the /bench page (issue #685, split
from #316; ``services/models/bench_scorecards.py``).

- ``GET /v1/bench/scorecards``  every card, newest first (any signed-in caller)

Read-only: the cards are files a person committed under ``bench/scorecards``
in the checkout ``BENCH_REPOSITORY_DIR`` names (empty: the backend's own
checkout). They describe the factory, not a company, so no company role is
asked; nothing here writes, runs the bench or asks a model.
"""

from __future__ import annotations

from pathlib import Path

from auth_policy import current_user_if_required, require_authenticated
from config import settings
from fastapi import APIRouter, Depends
from models import User
from services.models import bench_scorecards

router = APIRouter(prefix="/v1", tags=["Bench"])

# backend/app/routers/bench.py -> the repository's root, in a checkout.
_CHECKOUT = Path(__file__).resolve().parents[3]


def repository_root() -> Path:
    """The checkout the cards are read from."""
    configured = settings.BENCH_REPOSITORY_DIR.strip()
    return Path(configured) if configured else _CHECKOUT


@router.get("/bench/scorecards")
def get_bench_scorecards(
    user: User | None = Depends(current_user_if_required),  # noqa: B008 - FastAPI dependency marker.
):
    """Every committed scorecard, newest first, each valid, invalid or
    unreadable; a valid card's numbers as its scorer observed them."""
    require_authenticated(user)
    return bench_scorecards.history(repository_root())
