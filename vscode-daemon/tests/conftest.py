"""The daemon's own tests: its modules are imported by name, as ``python
daemon.py`` imports its siblings at run time."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
