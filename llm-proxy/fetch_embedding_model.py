"""Fetch the local embedding model named in ``embedding_model.json`` (issue #755).

Run at image build time (``llm-proxy/Dockerfile``) and, for a developer, once
by hand; the proxy itself never downloads anything. Each file is fetched from
the pinned revision of the model's repository, checked against the size and
sha256 in the manifest, and only then moved into place: a file that does not
match is deleted and the script exits non-zero, which fails the build.

    python fetch_embedding_model.py [target-directory]

The default target is ``models/<name>`` next to this script, the directory
``local_model.py`` reads when ``EMBEDDING_MODEL_DIR`` is not set. A file
already there with the pinned hash is kept and not fetched again.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "embedding_model.json"
BASE_URL = "https://huggingface.co"
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(manifest: dict, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, spec in manifest["files"].items():
        final = target / name
        if final.exists() and sha256_of(final) == spec["sha256"]:
            print(f"{name}: present, sha256 matches")
            continue
        url = f"{BASE_URL}/{manifest['repository']}/resolve/{manifest['revision']}/{spec['source']}"
        partial = target / f"{name}.partial"
        print(f"{name}: fetching {url}")
        with urllib.request.urlopen(url, timeout=600) as response, partial.open("wb") as out:
            for block in iter(lambda: response.read(CHUNK), b""):
                out.write(block)
        size, digest = partial.stat().st_size, sha256_of(partial)
        if size != spec["size"] or digest != spec["sha256"]:
            partial.unlink()
            raise SystemExit(
                f"{name}: got {size} bytes with sha256 {digest}; the manifest pins "
                f"{spec['size']} bytes with sha256 {spec['sha256']}"
            )
        partial.replace(final)
        print(f"{name}: {size} bytes, sha256 {digest} verified")
    (target / "NOTICE").write_text(
        f"{manifest['name']}: {manifest['repository']} at revision {manifest['revision']}, "
        f"licence {manifest['licence']}. Files and their sha256 are in embedding_model.json.\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = Path(argv[1]) if len(argv) > 1 else HERE / "models" / manifest["name"]
    fetch(manifest, target)


if __name__ == "__main__":
    main(sys.argv)
