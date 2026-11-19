"""Byte-range reads of a run's full log (issue #315, after PR #650).

``GET /v1/agent_runs/{run_id}/logs/{log_id}`` honours one ``Range: bytes=a-b``
or ``?offset=&limit=``: a 206 with ``Content-Range``, a 416 for a range past
the end. A piece cannot be checked against the whole log's sha256, so it is
served as ``X-Log-Integrity: unverified`` with ``X-Log-Verified: false`` and
``X-Log-Unverified-Reason: partial``, never as matched; the full read still
verifies as before. The object store has no byte-range read, so the backend
lists the object's chunks headers only and fetches just the chunks the range
touches (``run_log_reader.read_range``): the whole object is never read for
a range.
"""

import asyncio
import hashlib
import importlib.util
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from models.agent_run import Verification
from models.company import Company
from services.workspace import run_log_reader

NAME = "run-" + "b" * 32 + "/stdout"
CHUNK = 10
LOG = bytes(range(48, 48 + 75)) * 2  # 150 bytes, 15 chunks of 10
GOOD = hashlib.sha256(LOG).hexdigest()


class _Msg:
    def __init__(self, size, seq, pending):
        self.headers = {"Nats-Msg-Size": str(size)}
        self.metadata = SimpleNamespace(
            sequence=SimpleNamespace(stream=seq), num_pending=pending
        )


class _Sub:
    def __init__(self, messages):
        self._messages = messages
        self.unsubscribed = False

    @property
    def messages(self):
        async def gen():
            for msg in self._messages:
                yield msg

        return gen()

    async def unsubscribe(self):
        self.unsubscribed = True


class _Js:
    """A JetStream whose object store keeps each object in chunks of CHUNK
    bytes at stream sequences that are not contiguous (another upload's
    chunks sit between them), and that counts what it hands out."""

    def __init__(self, objects, size_of=None):
        self.objects = objects
        self.size_of = size_of or {}
        self.fetched: list[int] = []
        self.whole_reads = 0
        self.subscribed: list[dict] = []
        self.chunks: dict[int, bytes] = {}
        self.layout: dict[str, list[tuple[int, int]]] = {}
        seq = 1
        for name, data in objects.items():
            entries = []
            for at in range(0, len(data), CHUNK):
                seq += 2  # an interleaved chunk of something else
                self.chunks[seq] = data[at : at + CHUNK]
                entries.append((seq, len(data[at : at + CHUNK])))
            self.layout[name] = entries

    async def object_store(self, bucket):
        js = self

        class _Store:
            async def get_info(self, name):
                if name not in js.objects:
                    raise ObjectNotFoundError(name)
                size = js.size_of.get(name, len(js.objects[name]))
                return SimpleNamespace(size=size, deleted=False, nuid="N" + name[4:12])

            async def get(self, name, writeinto=None):
                js.whole_reads += 1
                writeinto.write(js.objects[name])

        return _Store()

    async def subscribe(self, subject, **kwargs):
        self.subscribed.append({"subject": subject, **kwargs})
        name = next(n for n in self.objects if subject.endswith("N" + n[4:12]))
        entries = self.layout[name]
        return _Sub(
            [
                _Msg(size, seq, len(entries) - i - 1)
                for i, (seq, size) in enumerate(entries)
            ]
        )

    async def get_msg(self, stream, seq=None, **kwargs):
        assert stream == "OBJ_B"
        self.fetched.append(seq)
        return SimpleNamespace(data=self.chunks[seq])


class ObjectNotFoundError(Exception):
    pass


def _nats(monkeypatch, client, js):
    for key, value in (("run_logs_bucket", "B"), ("js", js)):
        monkeypatch.setattr(client.app.state.nats_client, key, value, raising=False)


FULL = len(LOG)


def _run_with_log(session, client, monkeypatch, tmp_path, size=FULL):
    company = Company(company_name=f"Ranges {size}")
    session.add(company)
    session.commit()
    session.refresh(company)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path))
    payload = {
        "company_id": company.id,
        "external_ref": "github:example/app#2",
        "producer": "copilot",
        "artifact": "diff contents",
        "claims": {"summary": "opened a PR"},
    }
    run = client.post("/v1/agent_runs", json=payload).json()
    obj = {
        "stream": "stdout",
        "object": NAME,
        "observed": True,
        "sha256": GOOD,
        "size": size,
    }
    evidence = {
        "test_run": {
            "full_log": {"observed": True, "observed_by": "backend", "objects": [obj]}
        }
    }
    row = Verification(
        company_id=company.id, agent_run_id=run["id"], verdict="x", evidence=evidence
    )
    session.add(row)
    session.commit()
    return company.id, f"/v1/agent_runs/{run['id']}/logs/{row.id}-0"


@pytest.fixture
def ranged(sqlite_session, test_client, monkeypatch, tmp_path):
    company, url = _run_with_log(sqlite_session, test_client, monkeypatch, tmp_path)
    js = _Js({NAME: LOG})
    _nats(monkeypatch, test_client, js)

    def get(headers=None, **params):
        return test_client.get(
            url, params={"company_id": company, **params}, headers=headers or {}
        )

    return SimpleNamespace(get=get, js=js)


def _assert_partial(got, start, end):
    assert got.status_code == 206, got.text
    assert got.content == LOG[start : end + 1]
    assert got.headers["content-range"] == f"bytes {start}-{end}/{len(LOG)}"
    assert got.headers["content-length"] == str(end - start + 1)
    # A piece is never presented as checked against the whole log's hash.
    assert got.headers["x-log-integrity"] == "unverified"
    assert got.headers["x-log-verified"] == "false"
    assert got.headers["x-log-unverified-reason"] == "partial"
    assert "x-log-sha256-observed" not in got.headers
    assert got.headers["accept-ranges"] == "bytes"
    assert got.headers["x-log-sha256-recorded"] == GOOD
    assert got.headers["x-log-observed-by"] == "backend"


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-0", 0, 0),  # the first byte
        ("bytes=0-9", 0, 9),  # exactly the first chunk
        ("bytes=9-10", 9, 10),  # across a chunk edge
        ("bytes=149-149", 149, 149),  # the last byte
        ("bytes=140-", 140, 149),  # open-ended
        ("bytes=-25", 125, 149),  # the tail
        ("bytes=-1000", 0, 149),  # a suffix longer than the log covers all of it
        ("bytes=100-9999", 100, 149),  # an end past the log is clamped
    ],
)
def test_a_range_header_is_served_as_a_partial_unverified_read(
    ranged, header, start, end
):
    if (start, end) == (0, len(LOG) - 1):
        # A range that covers the whole log is the full read, verified.
        got = ranged.get({"Range": header})
        assert got.status_code == 200 and got.headers["x-log-integrity"] == "matched"
        return
    _assert_partial(ranged.get({"Range": header}), start, end)


def test_offset_and_limit_are_the_same_read_for_the_frontend(ranged):
    _assert_partial(ranged.get(offset=120, limit=20), 120, 139)
    _assert_partial(ranged.get(offset=145, limit=50), 145, 149)
    _assert_partial(ranged.get(limit=5), 0, 4)


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({"Range": "bytes=150-"}, {}),
        ({"Range": "bytes=500-600"}, {}),
        ({"Range": "bytes=-0"}, {}),
        ({}, {"offset": 150, "limit": 10}),
    ],
)
def test_a_range_past_the_end_is_416_with_the_size(ranged, headers, params):
    got = ranged.get(headers, **params)
    assert got.status_code == 416
    assert got.headers["content-range"] == f"bytes */{len(LOG)}"
    assert got.json()["detail"]["integrity"]["status"] == "unverified"
    assert ranged.js.fetched == []


@pytest.mark.parametrize(
    "header", ["bytes=5-1", "bytes=0-1,4-5", "items=0-4", "bytes=x-"]
)
def test_a_range_header_that_is_not_one_byte_range_is_ignored(ranged, header):
    """RFC 9110 §14.2: an invalid or multi-range header may be ignored; the
    answer is then the full read, verified as before."""
    got = ranged.get({"Range": header})
    assert got.status_code == 200
    assert got.headers["x-log-integrity"] == "matched"
    assert got.headers["x-log-verified"] == "true"
    assert got.headers["accept-ranges"] == "bytes"
    assert got.content == LOG


def test_both_a_range_header_and_an_offset_are_refused(ranged):
    got = ranged.get({"Range": "bytes=0-4"}, offset=0, limit=5)
    assert got.status_code == 400


def test_the_full_read_still_verifies(ranged):
    got = ranged.get()
    assert got.status_code == 200
    assert got.headers["x-log-integrity"] == "matched"
    assert got.headers["x-log-verified"] == "true"
    assert got.headers["x-log-sha256-observed"] == GOOD
    assert "x-log-unverified-reason" not in got.headers


def test_a_range_fetches_only_the_chunks_it_touches(ranged):
    """Memory and transfer follow the range, not the log: the object's chunks
    are listed headers only and only the ones the range overlaps are fetched."""
    _assert_partial(ranged.get({"Range": "bytes=95-104"}), 95, 104)
    seqs = [seq for seq, _ in ranged.js.layout[NAME]]
    assert ranged.js.fetched == [seqs[9], seqs[10]]
    assert ranged.js.whole_reads == 0
    listing = ranged.js.subscribed[0]
    assert listing["headers_only"] is True and listing["ordered_consumer"] is True
    assert listing["subject"] == "$O.B.C.N" + NAME[4:12]


def test_the_listing_stops_at_the_end_of_the_range():
    js = _Js({NAME: LOG})
    piece = asyncio.run(run_log_reader._read_range(js, "B", NAME, len(LOG), 0, 4))
    try:
        assert b"".join(piece.chunks()) == LOG[:5]
    finally:
        piece.spool.close()
    assert js.fetched == [js.layout[NAME][0][0]]


def test_a_store_size_that_differs_from_the_record_is_never_served_in_part(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company, url = _run_with_log(sqlite_session, test_client, monkeypatch, tmp_path)
    js = _Js({NAME: LOG}, size_of={NAME: len(LOG) + 1})
    _nats(monkeypatch, test_client, js)
    got = test_client.get(
        url, params={"company_id": company}, headers={"Range": "bytes=0-4"}
    )
    assert got.status_code == 409
    assert got.json()["detail"]["integrity"]["status"] == "unverified"
    assert js.fetched == []


def test_a_record_without_a_size_cannot_place_a_range_but_still_reads_whole(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company, url = _run_with_log(
        sqlite_session, test_client, monkeypatch, tmp_path, size=None
    )
    _nats(monkeypatch, test_client, _Js({NAME: LOG}))
    params = {"company_id": company}
    got = test_client.get(url, params=params, headers={"Range": "bytes=0-4"})
    assert got.status_code == 409
    assert got.json()["detail"]["integrity"]["status"] == "unverified"
    whole = test_client.get(url, params=params)
    assert whole.status_code == 200 and whole.headers["x-log-verified"] == "true"


def test_a_chunk_shorter_than_listed_is_unavailable():
    js = _Js({NAME: LOG})
    first = js.layout[NAME][0][0]
    js.chunks[first] = b"short"
    with pytest.raises(run_log_reader.LogUnavailable):
        asyncio.run(run_log_reader._read_range(js, "B", NAME, len(LOG), 0, 4))


def test_a_missing_object_is_410_for_a_range_too(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company, url = _run_with_log(sqlite_session, test_client, monkeypatch, tmp_path)
    _nats(monkeypatch, test_client, _Js({}))
    got = test_client.get(
        url, params={"company_id": company}, headers={"Range": "bytes=0-4"}
    )
    assert got.status_code == 410
    assert got.json()["detail"]["integrity"]["status"] == "missing"


def test_parse_range_rules():
    parse = run_log_reader.parse_range
    assert parse(None, None, None, 10) is None
    assert parse("bytes=2-4", None, None, 10) == (2, 4)
    assert parse(" bytes=7- ", None, None, 10) == (7, 9)
    assert parse(None, 3, None, 10) == (3, 9)
    for unsatisfiable in (("bytes=0-0", None, None, 0), (None, 0, 5, 0)):
        with pytest.raises(run_log_reader.RangeNotSatisfiable):
            parse(*unsatisfiable)


# ---------------------------------------------------------------------------
# Live: a real nats-server with JetStream and the generated permissions
# ---------------------------------------------------------------------------


def _nats_server() -> str | None:
    return os.environ.get("NATS_SERVER_BIN") or shutil.which("nats-server")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(_nats_server() is None, reason="no nats-server binary")
def test_live_a_range_reads_only_its_chunks_from_a_real_object_store(tmp_path):
    import nats
    import nats_client

    spec = importlib.util.spec_from_file_location(
        "nats_config",
        Path(__file__).resolve().parents[2] / "scripts" / "nats_config.py",
    )
    nats_config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nats_config)

    port = _free_port()
    conf = tmp_path / "nats.conf"
    conf.write_text(nats_config.render(nats_config.build()))
    env = {
        **os.environ,
        "NATS_BACKEND_PASSWORD": "backend-pw",
        "NATS_DAEMON_PASSWORD": "daemon-pw",
    }
    server = subprocess.Popen(
        [
            _nats_server(),
            "-p",
            str(port),
            "-js",
            "-sd",
            str(tmp_path / "js"),
            "-c",
            str(conf),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data = b"".join(b"line %06d\n" % i for i in range(60000))  # 660 000 bytes, 6 chunks

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)

        async def scenario():
            url = f"nats://127.0.0.1:{port}"
            backend = nats_client.NATSClient(url)
            backend.nc = await nats.connect(
                url,
                user="backend",
                password="backend-pw",
                inbox_prefix="_INBOX.backend",
            )
            daemon = await nats.connect(
                url, user="daemon", password="daemon-pw", inbox_prefix="_INBOX.daemon"
            )
            try:
                assert await backend.enable_jetstream()
                bucket = backend.run_logs_bucket
                # The daemon uploads (the backend may not write chunks).
                store = await daemon.jetstream().object_store(bucket)
                name = "run-" + "c" * 32 + "/stdout"
                await store.put(name, data)
                fetched = []
                get_msg = backend.js.get_msg

                async def counting(*args, **kwargs):
                    raw = await get_msg(*args, **kwargs)
                    if kwargs.get("seq"):  # a chunk; get_info reads the metadata
                        fetched.append(len(raw.data))
                    return raw

                backend.js.get_msg = counting
                cases = (
                    (0, 9, 1),
                    (131070, 131075, 2),
                    (len(data) - 50, len(data) - 1, 1),
                )
                for start, end, chunks in cases:
                    fetched.clear()
                    piece = await run_log_reader.read_range(
                        backend, name, len(data), start, end
                    )
                    assert b"".join(piece.chunks()) == data[start : end + 1]
                    # Only the chunks the range touches travel, never the log.
                    assert len(fetched) == chunks and sum(fetched) <= 2 * 128 * 1024
                whole = await run_log_reader.read(backend, name)
                assert (
                    hashlib.sha256(b"".join(whole.chunks())).hexdigest()
                    == hashlib.sha256(data).hexdigest()
                )
            finally:
                await daemon.close()
                await backend.nc.close()

        asyncio.run(asyncio.wait_for(scenario(), 60))
    finally:
        server.terminate()
        server.wait(timeout=10)
