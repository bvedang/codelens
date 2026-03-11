import sys
from types import ModuleType, SimpleNamespace

import pytest

from codelens.indexing.encoder import ColbertEncoder


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

    def encode(self, texts, *, is_query, query_length=None, document_length=None):
        type(self).encode_calls.append(
            {
                "texts": list(texts),
                "is_query": is_query,
                "query_length": query_length,
                "document_length": document_length,
            }
        )
        return [_FakeEmbedding([[1.0, 2.0]]) for _ in texts]


@pytest.fixture(autouse=True)
def _reset_fake_colbert():
    _FakeColBERT.init_kwargs = None
    _FakeColBERT.encode_calls = []


def test_colbert_encoder_sets_explicit_lengths(monkeypatch):
    pylate_module = ModuleType("pylate")
    pylate_module.models = SimpleNamespace(ColBERT=_FakeColBERT)
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
    }
    assert _FakeColBERT.encode_calls[1] == {
        "texts": ["gamma"],
        "is_query": True,
        "query_length": None,
        "document_length": None,
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
