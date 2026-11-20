"""
The local semantic embedding model (issue #755).

``EMBEDDING_PROVIDER=local`` (the default) embeds with multilingual-e5-small
on CPU in the proxy: semantic neighbours rank above unrelated text in Spanish
and in English, with every socket refused, so nothing is fetched or sent at
run time. When the model cannot load the hash vector stands in and ``/health``
says why; ``local-hash`` chooses the hash vector on purpose.

The tests that run the model need its files (``python
fetch_embedding_model.py`` once, or ``EMBEDDING_MODEL_DIR``) and are skipped
without them; the image always has them, fetched and verified at build time.
"""

import hashlib
import io
import json
import socket
import sys

import embedding
import fetch_embedding_model
import local_model
import pytest

MANIFEST = json.loads((local_model.HERE / "embedding_model.json").read_text(encoding="utf-8"))


def _model_present() -> bool:
    directory = local_model.model_dir()
    return all((directory / name).is_file() for name in MANIFEST["files"])


needs_model = pytest.mark.skipif(
    not _model_present(),
    reason="the embedding model is not fetched here (run llm-proxy/fetch_embedding_model.py)",
)


@pytest.fixture()
def no_network(monkeypatch):
    """Every socket connection and name lookup fails, as on a host with no route out."""

    def refuse(*args, **kwargs):
        raise AssertionError("the embedding model tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture()
def fresh_model(monkeypatch, no_network):
    """The model loaded anew inside the test, with the network refused."""
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    local_model.reset()
    yield local_model
    local_model.reset()


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def _vector(text):
    body = embedding._local(text)
    assert body["model"] == local_model.NAME
    return body["data"][0]["embedding"]


CASES = [
    # (text, its semantic neighbour, unrelated text)
    ("The deploy failed because the database migration timed out",
     "Rollout broke: the schema change took too long on Postgres",
     "I like baking bread on Sundays"),
    ("Add a login button to the header",
     "Put a sign-in control in the top navigation bar",
     "The cat sleeps on the sofa"),
    ("El despliegue falló porque la migración de la base de datos tardó demasiado",
     "La subida a producción se rompió: el cambio de esquema en Postgres excedió el tiempo",
     "Me gusta hornear pan los domingos"),
    ("Añadir un botón de inicio de sesión en la cabecera",
     "Poner un control para entrar en la barra de navegación superior",
     "El gato duerme en el sofá"),
    # Across the two languages: a memory written in one is found from the other.
    ("The tests fail on Windows paths",
     "Las pruebas fallan con las rutas de Windows",
     "Receta de paella valenciana"),
]


@needs_model
@pytest.mark.parametrize(("text", "near", "far"), CASES)
def test_semantic_neighbours_rank_above_unrelated_text(fresh_model, text, near, far):
    a, b, c = _vector(text), _vector(near), _vector(far)

    assert _cosine(a, b) > _cosine(a, c)


def test_the_hash_vector_does_not_find_the_paraphrase():
    # What #751's stand-in could not do, and the reason for the model.
    text, near, far = CASES[0]
    a, b, c = (embedding.fallback_embedding(t) for t in (text, near, far))

    assert _cosine(a, b) - _cosine(a, c) < 0.1


@needs_model
def test_the_endpoint_answers_the_models_vector_padded_to_the_column(fresh_model, proxy, client, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("an embedding left the proxy")

    monkeypatch.setattr(proxy.requests, "post", refuse)

    body = client.post("/v1/embeddings", json={"input": "una decisión del sprint"}).get_json()

    vector = body["data"][0]["embedding"]
    width = MANIFEST["dimensions"]
    assert body["model"] == "multilingual-e5-small"
    assert "usage" not in body
    assert len(vector) == embedding.DIMS
    assert any(vector[:width]) and not any(vector[width:])
    assert abs(_cosine(vector, vector) - 1.0) < 1e-5


@needs_model
def test_the_same_text_gets_the_same_vector(fresh_model):
    assert _vector("una memoria") == _vector("una memoria")


@needs_model
def test_health_says_on_when_the_model_is_loaded(fresh_model, proxy, client):
    caps = client.get("/health").get_json()["capabilities"]

    assert caps["embeddings"]["state"] == "on"
    assert caps["embeddings"]["provider"] == "local"
    assert caps["embeddings"]["model"] == "multilingual-e5-small"
    assert "nothing leaves the proxy" in caps["embeddings"]["reason"]


@needs_model
def test_a_model_failing_its_self_check_is_not_used(fresh_model, monkeypatch):
    monkeypatch.setattr(local_model._Model, "vector", lambda self, text: [1.0] + [0.0] * 383)

    reason = local_model.ensure_loaded()

    assert reason and "self-check" in reason
    assert embedding._local("x")["model"] == embedding.HASH_MODEL


# ── The fallback, which needs no model file ─────────────────────────────


def test_without_the_model_the_hash_vector_stands_in_and_health_says_why(model_unavailable, proxy, client,
                                                                        monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    body = client.post("/v1/embeddings", json={"input": "remember this"}).get_json()
    cap = client.get("/health").get_json()["capabilities"]["embeddings"]

    assert body["model"] == embedding.HASH_MODEL
    assert body["data"][0]["embedding"] == embedding.fallback_embedding("remember this")
    assert (cap["state"], cap["provider"], cap["model"]) == ("degraded", "local-hash", embedding.HASH_MODEL)
    assert "is missing" in cap["reason"] and "fetch_embedding_model.py" in cap["reason"]


def test_local_hash_is_the_hash_vector_on_purpose(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local-hash")
    monkeypatch.setattr(local_model, "embed", lambda *a, **k: pytest.fail("the model was used"))

    body = embedding._local("x")
    cap = embedding.capability("")

    assert body["model"] == embedding.HASH_MODEL
    assert cap["provider"] == "local-hash" and "EMBEDDING_PROVIDER=local-hash" in cap["reason"]


def _fake_model_dir(tmp_path, monkeypatch, *, pin_actual=True):
    """A model directory with stand-in files; the manifest pins their hashes when *pin_actual*."""
    files = {}
    for name in MANIFEST["files"]:
        content = f"not really {name}".encode()
        (tmp_path / name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest() if pin_actual else MANIFEST["files"][name]["sha256"]
        files[name] = {**MANIFEST["files"][name], "sha256": digest, "size": len(content)}
    monkeypatch.setattr(local_model, "MANIFEST", {**MANIFEST, "files": files})
    monkeypatch.setenv("EMBEDDING_MODEL_DIR", str(tmp_path))
    local_model.reset()


def test_a_file_that_does_not_match_its_pinned_hash_is_refused(tmp_path, monkeypatch):
    _fake_model_dir(tmp_path, monkeypatch, pin_actual=False)
    try:
        reason = local_model.ensure_loaded()
    finally:
        local_model.reset()

    assert "does not match the sha256" in reason


def test_a_missing_runtime_leaves_the_hash_vector(tmp_path, monkeypatch):
    _fake_model_dir(tmp_path, monkeypatch)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    try:
        reason = local_model.ensure_loaded()
        with pytest.raises(local_model.ModelUnavailable):
            local_model.embed("x", embedding.DIMS)
        body = embedding._local("x")
    finally:
        local_model.reset()

    assert "runtime is not installed" in reason
    assert body["model"] == embedding.HASH_MODEL


def test_a_runtime_error_on_one_text_still_answers_a_vector(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    def broken(text, dims):
        raise ValueError("bad input")

    monkeypatch.setattr(local_model, "embed", broken)

    assert embedding._local("x")["model"] == embedding.HASH_MODEL


# ── The build-time fetch ────────────────────────────────────────────────


def test_the_fetch_refuses_a_file_that_does_not_match_and_leaves_nothing(tmp_path, monkeypatch):
    urls = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return io.BytesIO(b"tampered")

    monkeypatch.setattr(fetch_embedding_model.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit, match="sha256"):
        fetch_embedding_model.fetch(MANIFEST, tmp_path)

    assert urls[0].startswith("https://huggingface.co/intfloat/multilingual-e5-small/resolve/")
    assert MANIFEST["revision"] in urls[0]
    assert not any(tmp_path.iterdir())


def test_the_fetch_keeps_a_file_whose_hash_matches(tmp_path, monkeypatch):
    content = b"pinned bytes"
    manifest = {**MANIFEST, "files": {"model.onnx": {"source": "onnx/x.onnx", "size": len(content),
                                                     "sha256": hashlib.sha256(content).hexdigest()}}}
    (tmp_path / "model.onnx").write_bytes(content)
    monkeypatch.setattr(fetch_embedding_model.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("fetched a file already present"))

    fetch_embedding_model.fetch(manifest, tmp_path)

    assert "MIT" in (tmp_path / "NOTICE").read_text(encoding="utf-8")


def test_the_manifest_pins_a_revision_and_every_file_by_sha256():
    assert len(MANIFEST["revision"]) == 40
    assert MANIFEST["licence"] == "MIT"
    for spec in MANIFEST["files"].values():
        assert len(spec["sha256"]) == 64 and spec["size"] > 0
