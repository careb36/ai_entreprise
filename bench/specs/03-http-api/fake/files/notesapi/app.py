"""The notes API as a WSGI application: POST/GET /notes, GET/DELETE /notes/<id>."""

import json
import re

from notesapi.store import NoteStore

MAX_BODY = 65536
_NOTE_PATH = re.compile(r"^/notes/([^/]+)$")
_REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    413: "Content Too Large",
}


def _answer(start_response, status, body=None, extra=()):
    headers = list(extra)
    data = b""
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.append(("Content-Type", "application/json"))
    headers.append(("Content-Length", str(len(data))))
    start_response(f"{status} {_REASONS[status]}", headers)
    return [data]


def _error(start_response, status, message, extra=()):
    return _answer(start_response, status, {"error": message}, extra)


def _read_text(environ):
    """The note's text from the request body, or (None, status, message)."""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY:
        return None, 413, f"the body is over {MAX_BODY} bytes"
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, 400, "the body is not JSON"
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text:
        return None, 400, 'the body needs a non-empty string "text"'
    return text, None, None


def make_app(store: NoteStore | None = None):
    store = store if store is not None else NoteStore()

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "") or "/"
        if path == "/notes":
            if method == "GET":
                return _answer(start_response, 200, store.all())
            if method == "POST":
                text, status, message = _read_text(environ)
                if text is None:
                    return _error(start_response, status, message)
                return _answer(start_response, 201, store.add(text))
            return _error(
                start_response,
                405,
                f"{method} is not allowed on /notes",
                [("Allow", "GET, POST")],
            )
        match = _NOTE_PATH.match(path)
        if match:
            if method not in ("GET", "DELETE"):
                return _error(
                    start_response,
                    405,
                    f"{method} is not allowed on {path}",
                    [("Allow", "GET, DELETE")],
                )
            raw_id = match.group(1)
            note_id = int(raw_id) if raw_id.isdigit() else None
            note = store.get(note_id) if note_id is not None else None
            if note is None:
                return _error(start_response, 404, f"no note {raw_id}")
            if method == "GET":
                return _answer(start_response, 200, note)
            store.delete(note_id)
            return _answer(start_response, 204)
        return _error(start_response, 404, f"no such path: {path}")

    return app
