import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from codelens.indexing.encoder import ColbertEncoder, _configure_tqdm_lock


class _FakeEmbedding:
    def __init__(self, value):
        self._value = value

    def tolist(self):
        return self._value


class _FakeColBERT:
    init_kwargs = None
    encode_calls = []

    def __init__(self, **kwargs):
        type(self).init_kwargs = kwargs

    def encode(
        self,
        texts,
        *,
        is_query,
        query_length=None,
        document_length=None,
        show_progress_bar=None,
    ):
        type(self).encode_calls.append(
            {
                "texts": list(texts),
                "is_query": is_query,
                "query_length": query_length,
                "document_length": document_length,
                "show_progress_bar": show_progress_bar,
            }
        )
        return [_FakeEmbedding([[1.0, 2.0]]) for _ in texts]


@pytest.fixture(autouse=True)
def _reset_fake_colbert():
    _FakeColBERT.init_kwargs = None
    _FakeColBERT.encode_calls = []


def test_colbert_encoder_sets_explicit_lengths(monkeypatch):
    pylate_module = ModuleType("pylate")
    setattr(pylate_module, "models", SimpleNamespace(ColBERT=_FakeColBERT))
    monkeypatch.setitem(sys.modules, "pylate", pylate_module)

    encoder = ColbertEncoder(
        model_name="lightonai/ColBERT-Zero",
        device="cpu",
        query_length=48,
        document_length=8192,
    )

    document_vectors = encoder.embed_documents(["alpha", "beta"])
    query_vectors = encoder.embed_queries(["gamma"])

    assert _FakeColBERT.init_kwargs == {
        "model_name_or_path": "lightonai/ColBERT-Zero",
        "device": "cpu",
        "query_length": 48,
        "document_length": 8192,
    }
    assert _FakeColBERT.encode_calls[0] == {
        "texts": ["alpha", "beta"],
        "is_query": False,
        "query_length": None,
        "document_length": None,
        "show_progress_bar": False,
    }
    assert _FakeColBERT.encode_calls[1] == {
        "texts": ["gamma"],
        "is_query": True,
        "query_length": None,
        "document_length": None,
        "show_progress_bar": False,
    }
    assert document_vectors == [[[1.0, 2.0]], [[1.0, 2.0]]]
    assert query_vectors == [[[1.0, 2.0]]]


def test_colbert_encoder_raises_clear_error_without_pylate(monkeypatch):
    monkeypatch.delitem(sys.modules, "pylate", raising=False)
    original_import = __import__

    def _guarded_import(name, *args, **kwargs):
        if name == "pylate":
            raise ImportError("missing pylate")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _guarded_import)

    with pytest.raises(RuntimeError, match="pylate is required"):
        ColbertEncoder()._get_model()


def test_colbert_encoder_close_drops_loaded_model(monkeypatch):
    pylate_module = ModuleType("pylate")
    setattr(pylate_module, "models", SimpleNamespace(ColBERT=_FakeColBERT))
    monkeypatch.setitem(sys.modules, "pylate", pylate_module)

    encoder = ColbertEncoder(device="cpu")
    encoder.embed_documents(["alpha"])

    assert encoder._model is not None

    encoder.close()
    encoder.close()

    assert encoder._model is None


def test_colbert_encoder_logs_model_load_and_first_batch(monkeypatch):
    events = []
    pylate_module = ModuleType("pylate")
    setattr(pylate_module, "models", SimpleNamespace(ColBERT=_FakeColBERT))
    monkeypatch.setitem(sys.modules, "pylate", pylate_module)
    monkeypatch.setattr(
        "codelens.indexing.encoder.log_event",
        lambda logger, level, message, **fields: events.append((message, fields)),
    )

    encoder = ColbertEncoder(device="cpu")
    encoder.embed_documents(["alpha", "beta"])

    messages = [message for message, _fields in events]
    assert "Loading ColBERT model" in messages
    assert "Loaded ColBERT model" in messages
    assert "Starting document embedding" in messages
    assert "Finished document embedding" in messages


def test_configure_tqdm_lock_replaces_default_write_lock(monkeypatch):
    class _DefaultLock:
        pass

    class _FakeTqdm:
        _lock = _DefaultLock()
        last_lock = None

        @classmethod
        def set_lock(cls, lock):
            cls.last_lock = lock
            cls._lock = lock

    tqdm_module = ModuleType("tqdm")
    setattr(tqdm_module, "tqdm", _FakeTqdm)
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_module)

    _DefaultLock.__name__ = "TqdmDefaultWriteLock"
    _configure_tqdm_lock()

    assert isinstance(_FakeTqdm.last_lock, type(threading.RLock()))


def test_configure_tqdm_lock_preserves_existing_custom_lock(monkeypatch):
    existing_lock = threading.RLock()

    class _FakeTqdm:
        _lock = existing_lock
        set_lock_calls = 0

        @classmethod
        def set_lock(cls, lock):
            cls.set_lock_calls += 1
            cls._lock = lock

    tqdm_module = ModuleType("tqdm")
    setattr(tqdm_module, "tqdm", _FakeTqdm)
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_module)

    _configure_tqdm_lock()

    assert _FakeTqdm.set_lock_calls == 0
    assert _FakeTqdm._lock is existing_lock
