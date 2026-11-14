import io
import json
from wsgiref.util import setup_testing_defaults

from notesapi.app import make_app


def _call(app, method, path, body=None):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, CONTENT_LENGTH=str(len(raw)))
    environ["wsgi.input"] = io.BytesIO(raw)
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = int(status.split()[0])

    data = b"".join(app(environ, start_response))
    return seen["status"], json.loads(data) if data else None


def test_create_read_delete():
    app = make_app()
    assert _call(app, "POST", "/notes", {"text": "x"}) == (201, {"id": 1, "text": "x"})
    assert _call(app, "GET", "/notes/1") == (200, {"id": 1, "text": "x"})
    assert _call(app, "DELETE", "/notes/1") == (204, None)
    assert _call(app, "GET", "/notes") == (200, [])


def test_errors():
    app = make_app()
    assert _call(app, "POST", "/notes", {"text": ""})[0] == 400
    assert _call(app, "GET", "/notes/5")[0] == 404
    assert _call(app, "PATCH", "/notes")[0] == 405
    assert _call(app, "GET", "/nothing")[0] == 404


def test_body_over_the_limit_is_refused():
    app = make_app()
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD="POST", PATH_INFO="/notes", CONTENT_LENGTH="70000")
    environ["wsgi.input"] = io.BytesIO(b"")
    seen = {}
    app(
        environ,
        lambda status, headers, exc_info=None: seen.setdefault("status", status),
    )
    assert seen["status"].startswith("413")
