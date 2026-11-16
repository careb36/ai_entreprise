"""What the tests of the symbol part of the ``grounded`` check share (issue
#444): the real daemon loaded from ``vscode-daemon/daemon.py``. The tests are
``test_grounding_signatures.py``, ``test_grounding_signature_check.py`` and
``test_grounding_signature_deliver.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location("fabrique_daemon_sig", _DAEMON_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon():
    return _load_daemon()
