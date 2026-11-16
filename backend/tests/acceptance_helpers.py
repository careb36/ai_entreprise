"""What the tests of acceptance tests per criterion share: the real daemon
loaded from ``vscode-daemon/daemon.py``, the normalized specification, the
acceptance tests and implementations a task delivers, a producer's answer
and the Verifier's checks of a task. The tests are ``test_acceptance*.py``;
``test_git_acceptance.py`` imports these names from ``test_acceptance``, which
re-exports them.
"""

import importlib.util
import textwrap
from pathlib import Path

from models.agent_run import AgentRun, Verification
from services.planning import spec_check
from sqlmodel import select
from test_specifications import GOOD

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "vscode-daemon" / "daemon.py"


def _load_daemon():
    spec = importlib.util.spec_from_file_location(
        "fabrique_daemon_acceptance", _DAEMON_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the test-writing tasks in a plan ---------------------------------------

SPEC = spec_check.normalize(GOOD)


def _answer(files: dict[str, str]) -> str:
    blocks = "".join(
        f"```python path={path}\n{body}```\n\n" for path, body in files.items()
    )
    return f"Delivered.\n\n{blocks}"


ACCEPTANCE_TESTS = textwrap.dedent(
    """\
    def test_req_1_1_lists_the_tasks():
        from src.board import tasks_of

        assert tasks_of("acme") == ["a", "b"]


    def test_req_1_2_one_row_per_task():
        from src.board import tasks_of

        assert len(tasks_of("acme")) == 2
    """
)


BOARD = 'def tasks_of(company):\n    return ["a", "b"]\n'


MOVE_TESTS = (
    "def test_req_2_1_drop_on_done():\n    from src.move import move\n\n"
    '    assert move("t", "done") == "done"\n'
)


def _checks(session, item):
    session.expire_all()
    run = session.exec(
        select(AgentRun)
        .where(AgentRun.external_ref == f"task-{item.id}")
        .order_by(AgentRun.version.desc())
    ).first()
    verification = session.exec(
        select(Verification)
        .where(Verification.agent_run_id == run.id)
        .order_by(Verification.id.desc())
    ).first()
    return verification, {c["name"]: c for c in verification.checks}
