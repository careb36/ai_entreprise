"""The bench's broker is its own (issue #316).

``broker.py`` refuses the default port and a broker where a daemon answers,
accepts a quiet one of its own, and says when one cannot be reached.

No stack here: ``scripts/bench/run.sh`` drives the factory; these tests check
the pieces it relies on. The shared readers and rows are in ``bench_helpers``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "bench"))
import broker  # scripts/bench/broker.py, by path


class _FakeNats:
    def __init__(self, answers):
        self.answers = answers
        self.closed = False

    async def request(self, subject, payload, timeout):
        assert subject == "workspace.status"
        if not self.answers:
            raise TimeoutError("no responders")
        return object()

    async def close(self):
        self.closed = True


def _connect(answers=False, reachable=True):
    async def connect(url, **kwargs):
        if not reachable:
            raise ConnectionRefusedError("refused")
        return _FakeNats(answers)

    return connect


@pytest.mark.parametrize(
    "url", ["nats://127.0.0.1:4222", "nats://localhost", "127.0.0.1:4222"]
)
def test_the_default_port_is_refused(url):
    status, message = broker.check(url, connect=_connect())
    assert status == 1
    assert "default NATS port" in message


def test_a_broker_where_a_daemon_answers_is_refused():
    status, message = broker.check(
        "nats://127.0.0.1:54222", connect=_connect(answers=True)
    )
    assert status == 1
    assert "already answers" in message


def test_a_quiet_broker_of_its_own_is_accepted():
    assert broker.check("nats://127.0.0.1:54222", connect=_connect())[0] == 0


def test_an_unreachable_broker_is_said_so():
    status, message = broker.check(
        "nats://127.0.0.1:54222", connect=_connect(reachable=False)
    )
    assert status == 2
    assert "cannot reach" in message
