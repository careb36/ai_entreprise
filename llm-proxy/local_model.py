"""
The local semantic embedding model: multilingual-e5-small on CPU (issue #755).

DeepSeek has no embeddings endpoint, so memory is embedded here, in the proxy,
by a small multilingual sentence-embedding model run with ONNX Runtime: no
key, no outside service, and no call to the internet at run time. The model
file and its tokenizer are fetched at image build time from a pinned revision
and checked against the sha256 in ``embedding_model.json``
(``fetch_embedding_model.py``); they are checked again here before the model
is loaded, so a file changed after the build is refused rather than used.

What is run, and why (the evidence is in ``docs/CONFIGURATION.md``,
``EMBEDDING_PROVIDER``): ``intfloat/multilingual-e5-small`` (MIT), its int8
ONNX export (118 MB), 384 values per text, truncated at 512 tokens. Text is
prefixed with ``"query: "`` (the model's convention for symmetric similarity,
which is what memory search is: a memory is compared with a memory), mean
pooled over the tokens and normalised. The 384 values are padded with zeros to
the ``DIMS`` of ``vector_memory.embedding``; zeros change neither a vector's
norm nor the cosine distance between two vectors padded alike.

Nothing here raises past ``embed``'s ``ModelUnavailable``: a missing file, a
wrong hash, a missing runtime or a model that fails its self-check leaves the
model unloaded with the reason, and ``embedding.py`` uses the hash vector
instead and says so in ``/health``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("llm-proxy")

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "embedding_model.json").read_text(encoding="utf-8"))
NAME = MANIFEST["name"]
_CHUNK = 1 << 20

# The self-check run once at load: each text must be nearer its paraphrase than
# the unrelated text, in English and in Spanish. A model that cannot do that is
# not used (a damaged file, or a CPU whose int8 kernels lose too much).
SELF_CHECK = (
    ("The deploy failed because the database migration timed out",
     "Rollout broke: the schema change took too long on Postgres",
     "I like baking bread on Sundays"),
    ("Añadir un botón de inicio de sesión en la cabecera",
     "Poner un control para entrar en la barra de navegación superior",
     "El gato duerme en el sofá"),
)


class ModelUnavailable(RuntimeError):
    """The local model is not loaded; the message says why."""


def model_dir() -> Path:
    """Where the model files are: ``EMBEDDING_MODEL_DIR``, else ``models/<name>`` here."""
    configured = os.getenv("EMBEDDING_MODEL_DIR", "").strip()
    return Path(configured) if configured else HERE / "models" / NAME


def _threads() -> int:
    try:
        return max(1, int(os.getenv("EMBEDDING_THREADS", "2")))
    except ValueError:
        return 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


class _Model:
    def __init__(self, session, tokenizer, numpy):
        self._session = session
        self._tokenizer = tokenizer
        self._np = numpy
        self._inputs = {i.name for i in session.get_inputs()}

    def vector(self, text: str) -> list[float]:
        np = self._np
        encoding = self._tokenizer.encode(MANIFEST["prefix"] + text)
        ids = np.array([encoding.ids], dtype=np.int64)
        mask = np.array([encoding.attention_mask], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        values = pooled[0]
        norm = float(np.linalg.norm(values)) or 1.0
        return [float(v) / norm for v in values]


_lock = threading.Lock()
_model: _Model | None = None
_reason: str | None = None
_tried = False


def _load() -> tuple[_Model | None, str | None]:
    directory = model_dir()
    for name, spec in MANIFEST["files"].items():
        path = directory / name
        if not path.is_file():
            return None, (f"{path} is missing (the image fetches it at build time; elsewhere run "
                          "llm-proxy/fetch_embedding_model.py)")
        if _sha256(path) != spec["sha256"]:
            return None, f"{path} does not match the sha256 pinned in embedding_model.json"
    try:
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer
    except ImportError as exc:
        return None, f"the model runtime is not installed ({exc.name})"
    try:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = _threads()
        options.inter_op_num_threads = 1
        session = onnxruntime.InferenceSession(str(directory / "model.onnx"), options,
                                               providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=int(MANIFEST["max_tokens"]))
        tokenizer.no_padding()
        model = _Model(session, tokenizer, numpy)
        for text, near, far in SELF_CHECK:
            a, b, c = (model.vector(t) for t in (text, near, far))
            if len(a) != MANIFEST["dimensions"]:
                return None, f"the model answered {len(a)} values, not {MANIFEST['dimensions']}"
            if sum(x * y for x, y in zip(a, b)) <= sum(x * y for x, y in zip(a, c)):
                return None, "the model failed its self-check (a paraphrase ranked below unrelated text)"
    except Exception as exc:  # noqa: BLE001 - any runtime failure leaves the hash vector in place
        return None, f"the model could not be loaded ({type(exc).__name__}: {exc})"
    return model, None


def ensure_loaded() -> str | None:
    """Load the model once per process; ``None`` when loaded, else the reason it is not."""
    global _model, _reason, _tried
    with _lock:
        if not _tried:
            _model, _reason = _load()
            _tried = True
            if _model is None:
                logger.warning("Local embedding model %s not loaded: %s", NAME, _reason)
            else:
                logger.info("Local embedding model %s loaded from %s", NAME, model_dir())
        return _reason


def embed(text: str, dims: int) -> list[float]:
    """*text*'s vector, padded with zeros to *dims*; ``ModelUnavailable`` when not loaded."""
    reason = ensure_loaded()
    if _model is None:
        raise ModelUnavailable(reason or "not loaded")
    values = _model.vector(text)
    return values + [0.0] * (dims - len(values))


def reset() -> None:
    """Forget the loaded model, so the next call loads again (tests)."""
    global _model, _reason, _tried
    with _lock:
        _model, _reason, _tried = None, None, False
