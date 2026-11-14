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

    data = b"".join(app(environ, start_response))
    return seen["status"], data


def test_req_2_1_deletes_a_note():
    from notesapi.app import make_app

    app = make_app()
    _call(app, "POST", "/notes", {"text": "buy milk"})
    assert _call(app, "DELETE", "/notes/1") == (204, b"")
    assert _call(app, "GET", "/notes/1")[0] == 404
