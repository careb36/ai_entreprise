#!/usr/bin/env python3
"""Refresh the model catalogue snapshot the backend ships with.

The backend reads models.dev (https://models.dev/api.json, MIT) at run time
when it can (``services/model_catalog.py``) and falls back to this snapshot
when it cannot: offline, in tests, behind a firewall. The snapshot keeps the
providers in ``SNAPSHOT_PROVIDERS`` and, for each model, only the fields
La Fabrique uses (capabilities, limits, price).

    python3 scripts/update_models_snapshot.py            # from models.dev
    python3 scripts/update_models_snapshot.py api.json   # from a saved copy
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Loaded by path: the services package imports the whole backend.
_spec = importlib.util.spec_from_file_location(
    "model_catalog", ROOT / "backend" / "app" / "services" / "model_catalog.py"
)
_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_catalog)
SNAPSHOT_PATH, SNAPSHOT_PROVIDERS = _catalog.SNAPSHOT_PATH, _catalog.SNAPSHOT_PROVIDERS
slim_catalog = _catalog.slim_catalog

SOURCE = "https://models.dev/api.json"


def main() -> int:
    if len(sys.argv) > 1:
        raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(SOURCE, timeout=60) as response:
            raw = json.load(response)
    catalog = slim_catalog(raw, only=SNAPSHOT_PROVIDERS)
    SNAPSHOT_PATH.write_text(
        json.dumps(catalog, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    models = sum(len(p["models"]) for p in catalog["providers"].values())
    print(
        f"{SNAPSHOT_PATH.relative_to(ROOT)}: {len(catalog['providers'])} providers, {models} models"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
