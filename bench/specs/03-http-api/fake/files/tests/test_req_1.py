import io
import json
from wsgiref.util import setup_testing_defaults


def _call(app, method, path, body=None):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, CONTENT_LENGTH=str(len(raw)))
    environ["wsgi.input"] = io.BytesIO(raw)
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = int(status.split()[0])
        seen["type"] = dict(headers).get("Content-Type", "")

    data = b"".join(app(environ, start_response))
    return seen["status"], seen["type"], json.loads(data) if data else None


def test_req_1_1_creates_a_note():
    from notesapi.app import make_app

    status, kind, body = _call(make_app(), "POST", "/notes", {"text": "buy milk"})
    assert (status, body) == (201, {"id": 1, "text": "buy milk"})
    assert kind.startswith("application/json")


def test_req_1_2_reads_a_note():
    from notesapi.app import make_app

    app = make_app()
    _call(app, "POST", "/notes", {"text": "buy milk"})
    assert _call(app, "GET", "/notes/1")[::2] == (200, {"id": 1, "text": "buy milk"})


def test_req_1_3_lists_notes():
    from notesapi.app import make_app

    app = make_app()
    _call(app, "POST", "/notes", {"text": "a"})
    _call(app, "POST", "/notes", {"text": "b"})
    assert _call(app, "GET", "/notes")[::2] == (
        200,
        [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}],
    )
