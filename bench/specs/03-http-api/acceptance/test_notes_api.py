"""Acceptance tests of the notes HTTP API, written by a person.

They grade what the factory produced; the model under test never sees or
edits them. Each test is named after the criterion it decides
(``test_req_1_1_...`` decides REQ-1.1); a criterion passes when every test
named after it passes. The project under test is on ``PYTHONPATH``. The
application is called as a WSGI callable with a constructed environ: no
socket is opened.
"""

import io
import json
from wsgiref.util import setup_testing_defaults

import pytest


def _app():
    from notesapi.app import make_app

    return make_app()


def _call(app, method, path, body=None):
    """(status code, headers as a lower-cased dict, body bytes)."""
    raw = (
        body
        if isinstance(body, bytes)
        else b""
        if body is None
        else json.dumps(body).encode()
    )
    environ = {}
    setup_testing_defaults(environ)
    environ.update(
        {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        }
    )
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = int(status.split()[0])
        seen["headers"] = {k.lower(): v for k, v in headers}

    chunks = app(environ, start_response)
    try:
        data = b"".join(chunks)
    finally:
        if hasattr(chunks, "close"):
            chunks.close()
    return seen["status"], seen["headers"], data


def _json(data):
    return json.loads(data.decode("utf-8"))


def test_req_1_1_post_creates_a_note():
    status, headers, data = _call(_app(), "POST", "/notes", {"text": "buy milk"})
    assert status == 201
    assert headers["content-type"].startswith("application/json")
    assert _json(data) == {"id": 1, "text": "buy milk"}


def test_req_1_1_ids_count_up_from_one():
    app = _app()
    _call(app, "POST", "/notes", {"text": "a"})
    status, _, data = _call(app, "POST", "/notes", {"text": "b"})
    assert status == 201
    assert _json(data) == {"id": 2, "text": "b"}


def test_req_1_1_each_application_has_its_own_store():
    _call(_app(), "POST", "/notes", {"text": "elsewhere"})
    status, _, data = _call(_app(), "POST", "/notes", {"text": "here"})
    assert (status, _json(data)) == (201, {"id": 1, "text": "here"})


def test_req_1_2_get_reads_a_note():
    app = _app()
    _call(app, "POST", "/notes", {"text": "buy milk"})
    status, headers, data = _call(app, "GET", "/notes/1")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    assert _json(data) == {"id": 1, "text": "buy milk"}


def test_req_1_3_get_lists_notes_in_creation_order():
    app = _app()
    _call(app, "POST", "/notes", {"text": "a"})
    _call(app, "POST", "/notes", {"text": "b"})
    status, _, data = _call(app, "GET", "/notes")
    assert status == 200
    assert _json(data) == [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]


def test_req_1_3_an_empty_store_lists_nothing():
    status, _, data = _call(_app(), "GET", "/notes")
    assert (status, _json(data)) == (200, [])


def test_req_2_1_delete_removes_the_note():
    app = _app()
    _call(app, "POST", "/notes", {"text": "buy milk"})
    status, _, data = _call(app, "DELETE", "/notes/1")
    assert status == 204
    assert data == b""
    assert _call(app, "GET", "/notes/1")[0] == 404
    assert _json(_call(app, "GET", "/notes")[2]) == []


def test_req_2_1_a_deleted_id_is_not_reused():
    app = _app()
    _call(app, "POST", "/notes", {"text": "a"})
    _call(app, "DELETE", "/notes/1")
    status, _, data = _call(app, "POST", "/notes", {"text": "b"})
    assert (status, _json(data)) == (201, {"id": 2, "text": "b"})


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps([1, 2]).encode(),
        json.dumps({}).encode(),
        json.dumps({"text": ""}).encode(),
        json.dumps({"text": 7}).encode(),
    ],
)
def test_req_3_1_a_bad_body_is_refused(body):
    app = _app()
    status, headers, data = _call(app, "POST", "/notes", body)
    assert status == 400
    assert headers["content-type"].startswith("application/json")
    assert "error" in _json(data)
    assert _json(_call(app, "GET", "/notes")[2]) == []


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_req_3_2_an_unknown_id_is_not_found(method):
    app = _app()
    _call(app, "POST", "/notes", {"text": "a"})
    status, _, data = _call(app, method, "/notes/99")
    assert status == 404
    assert "error" in _json(data)


def test_req_3_2_a_non_numeric_id_is_not_found():
    status, _, data = _call(_app(), "GET", "/notes/abc")
    assert status == 404
    assert "error" in _json(data)


def test_req_3_3_an_unknown_path_is_not_found():
    status, _, data = _call(_app(), "GET", "/other")
    assert status == 404
    assert "error" in _json(data)


def test_req_3_3_a_method_not_allowed():
    app = _app()
    status, _, data = _call(app, "PUT", "/notes", {"text": "a"})
    assert status == 405
    assert "error" in _json(data)
    assert _json(_call(app, "GET", "/notes")[2]) == []
