"""Which image a sandboxed container ran: the daemon's own observation.

Before a container run (``sandbox.run_sandboxed``, ``app_run``) the daemon
asks the runtime for the image's content ID and registry digest with
``image inspect`` (Docker and Podman answer the same JSON shape), and runs the
container from that ID, so a tag that moves between the observation and the
run cannot change what ran. An image not yet on the machine is pulled by the
run itself and observed after it, said so (``observed: after_run``).

The report's ``image_identity`` is what the daemon saw, never what a producer
said: ``id`` (``sha256:`` of the image's configuration, which names exactly
what ran), ``digest`` (a ``RepoDigests`` entry, preferring the reference's own
repository; ``None`` with ``digest_reason`` for an image built or loaded on
the machine), ``observed_by: daemon`` and ``observed``. With no ID (host and
bwrap modes, or an answer the runtime could not give) it says why in
``reason``: never an empty field read as success.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

# Each ``image inspect`` call.
INSPECT_SECONDS = 30
_HEX_ID = re.compile(r"^[0-9a-f]{64}$")


def no_image(reason: str) -> dict[str, Any]:
    """An ``image_identity`` with no image: the reason, never an empty success."""
    return {"id": None, "digest": None, "reason": reason}


def repository(image: str) -> str:
    """The repository part of a reference, without digest or tag (a
    registry's port stays: a tag is only after the last slash)."""
    ref = image.split("@", 1)[0]
    head, _, last = ref.rpartition("/")
    last = last.split(":", 1)[0]
    return f"{head}/{last}" if head else last


def identity(runtime: str, image: str) -> dict[str, Any]:
    """The image as the runtime has it now; ``run_id`` is its ID as the
    runtime spells it (Podman leaves out ``sha256:``), to run from."""
    tool = os.path.basename(runtime)
    try:
        done = subprocess.run(
            [runtime, "image", "inspect", image],
            capture_output=True, text=True, timeout=INSPECT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return no_image(f"{tool} image inspect did not answer: {exc}")
    if done.returncode != 0:
        said = (done.stderr or "").strip()[-200:] or f"exit {done.returncode}"
        return no_image(f"{tool} has no image {image!r} on this machine ({said})")
    try:
        entries = json.loads(done.stdout or "")
        entry = entries[0] if isinstance(entries, list) else entries
        raw_id = str(entry.get("Id") or "").strip()
        digests = [d for d in entry.get("RepoDigests") or [] if isinstance(d, str) and "@sha256:" in d]
    except (ValueError, IndexError, AttributeError, TypeError):
        return no_image(f"{tool} image inspect's answer could not be read")
    image_id = f"sha256:{raw_id}" if _HEX_ID.match(raw_id) else raw_id
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        return no_image(f"{tool} reported no content ID for {image!r}")
    repo = repository(image)
    names = (repo, f"docker.io/{repo}", f"docker.io/library/{repo}")
    own = [d for d in digests if d.split("@", 1)[0] in names]
    out: dict[str, Any] = {
        "id": image_id,
        "digest": (own or digests or [None])[0],
        "observed_by": "daemon",
        "run_id": raw_id,
    }
    if out["digest"] is None:
        out["digest_reason"] = "the image has no registry digest (built or loaded on this machine)"
    return out


def pinned(runtime: str, box: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The box to run, with the image's content ID in place of the tag when
    the runtime has the image (``observed: before_run``), and the identity."""
    seen = identity(runtime, box["image"])
    if not seen.get("id"):
        return box, seen
    run_id = seen.pop("run_id")
    return {**box, "image": run_id}, {**seen, "observed": "before_run"}


def observed_after(runtime: str, image: str, before: dict[str, Any]) -> dict[str, Any]:
    """The identity to report: the one observed before the run, else the
    image *image* names as the run left it (pulled by the run), said so."""
    if before.get("id"):
        return before
    after = identity(runtime, image)
    after.pop("run_id", None)
    if after.get("id"):
        return {
            **after,
            "observed": "after_run",
            "note": "the image was not on the machine before the run; the run pulled it and it was observed after",
        }
    return {**after, "before_run": before.get("reason")}
