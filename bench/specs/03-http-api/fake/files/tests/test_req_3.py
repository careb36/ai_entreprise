import io
import json
from wsgiref.util import setup_testing_defaults


def _call(app, method, path, raw=b""):
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD=method, PATH_INFO=path, CONTENT_LENGTH=str(len(raw)))
    environ["wsgi.input"] = io.BytesIO(raw)
    seen = {}

    def start_response(status, headers, exc_info=None):
        seen["status"] = int(status.split()[0])

    data = b"".join(app(environ, start_response))
    return seen["status"], json.loads(data) if data else None


def test_req_3_1_refuses_a_bad_body():
    from notesapi.app import make_app

    app = make_app()
    for raw in (b"{", b'{"text": ""}', b"[]"):
        status, body = _call(app, "POST", "/notes", raw)
        assert status == 400 and "error" in body
    assert _call(app, "GET", "/notes") == (200, [])


def test_req_3_2_unknown_id():
    from notesapi.app import make_app

    app = make_app()
    for method in ("GET", "DELETE"):
        status, body = _call(app, method, "/notes/99")
        assert status == 404 and "error" in body


def test_req_3_3_unknown_path_and_method():
    from notesapi.app import make_app

    app = make_app()
    status, body = _call(app, "GET", "/other")
    assert status == 404 and "error" in body
    status, body = _call(app, "PUT", "/notes")
    assert status == 405 and "error" in body
