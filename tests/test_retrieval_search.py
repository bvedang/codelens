from contextlib import contextmanager
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from codelens.chunker import parse_java
from codelens.indexing.documents import build_index_documents, document_payload
from codelens.indexing.faiss_repository import FaissIndexRepository
from codelens.retrieval.search import RetrievalSearchService


class _FakeIndex:
    def __init__(self, vector_size):
        self.d = vector_size
        self.vectors = []

    def add(self, rows):
        for row in rows.tolist():
            self.vectors.append(list(row))

    def reconstruct(self, index):
        return self.vectors[index]

    def search(self, queries, limit):
        scores = []
        indices = []
        for query in queries.tolist():
            ranked = sorted(
                (
                    (
                        sum(float(a) * float(b) for a, b in zip(query, vector)),
                        index,
                    )
                    for index, vector in enumerate(self.vectors)
                ),
                reverse=True,
            )[:limit]
            padded_scores = [score for score, _ in ranked]
            padded_indices = [index for _, index in ranked]
            while len(padded_scores) < limit:
                padded_scores.append(float("-inf"))
                padded_indices.append(-1)
            scores.append(padded_scores)
            indices.append(padded_indices)
        return scores, indices


class _FakeFaiss:
    def __init__(self):
        self.saved = {}

    def IndexFlatIP(self, vector_size):
        return _FakeIndex(vector_size)

    def write_index(self, index, path):
        self.saved[path] = index

    def read_index(self, path):
        return self.saved[path]


class _FakeQueryEncoder:
    model_name = "fake-query-encoder"

    def embed_queries(self, texts):
        assert texts == ["refund payment cancel"]
        return [[[1.0, 0.0]]]


def test_search_code_returns_ranked_compact_hits(tmp_path):
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    repository = FaissIndexRepository(tmp_path, faiss_module=_FakeFaiss())
    with patch(
        "codelens.indexing.faiss_repository.get_session", side_effect=_test_session
    ):
        repository.save(
            entries=[
                {
                    "chunk_id": "chunk-1",
                    "file_path": "src/OrderService.java",
                    "chunk_kind": "method",
                    "package": "com.app.orders",
                    "owner_chain": ["OrderService"],
                    "name": "cancelOrder",
                    "signature": "public void cancelOrder(Long orderId)",
                    "start_line": 15,
                    "end_line": 20,
                    "source_set": ":orders:main",
                    "retrieval_text": "cancel order refund paymentGateway.refund",
                    "source_text": (
                        "public void cancelOrder(Long orderId) { "
                        "paymentGateway.refund(order.getPaymentId(), order.getTotal()); "
                        "}"
                    ),
                },
                {
                    "chunk_id": "chunk-2",
                    "file_path": "src/OrderService.java",
                    "chunk_kind": "method",
                    "package": "com.app.orders",
                    "owner_chain": ["OrderService"],
                    "name": "findOrder",
                    "signature": "public Order findOrder(Long orderId)",
                    "start_line": 22,
                    "end_line": 24,
                    "source_set": ":orders:main",
                    "retrieval_text": "find order lookup",
                    "source_text": "public Order findOrder(Long orderId) { return orderRepo.findById(orderId); }",
                },
            ],
            vectors=[
                [[1.0, 0.0]],
                [[0.2, 0.0]],
            ],
            model_name="ColBERT-Zero-supervised",
            indexed_at="2026-03-20T00:00:00+00:00",
        )

        service = RetrievalSearchService(repository, _FakeQueryEncoder())
        result = service.search_code(
            "refund payment cancel",
            repo_root=str(tmp_path),
            top_k=2,
        )

    assert result.returned_count == 2
    assert result.has_more is False
    assert result.results[0].chunk_id == "chunk-1"
    assert result.results[0].kind == "method"
    assert result.results[0].symbol == "com.app.orders.OrderService.cancelOrder"
    assert result.results[0].source_set == ":orders:main"
    assert "semantic" in result.results[0].why_matched
    assert any(reason.startswith("terms:") for reason in result.results[0].why_matched)
    assert "paymentGateway.refund" in result.results[0].summary
    assert result.results[0].start_line == 15
    assert result.results[0].end_line == 20
    assert result.results[1].start_line == 22
    assert result.results[1].end_line == 24
    assert result.results[0].score > result.results[1].score


def test_search_code_returns_empty_results_for_blank_query(tmp_path):
    repository = FaissIndexRepository(tmp_path, faiss_module=_FakeFaiss())
    service = RetrievalSearchService(repository, _FakeQueryEncoder())

    result = service.search_code("   ", repo_root=str(tmp_path))

    assert result.returned_count == 0
    assert result.results == ()


def test_read_code_returns_target_chunk_with_same_file_neighbors(tmp_path):
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    repository = FaissIndexRepository(tmp_path, faiss_module=_FakeFaiss())
    with patch(
        "codelens.indexing.faiss_repository.get_session", side_effect=_test_session
    ):
        repository.save(
            entries=[
                {
                    "chunk_id": "chunk-1",
                    "file_path": "src/OrderService.java",
                    "chunk_kind": "method",
                    "package": "com.app.orders",
                    "owner_chain": ["OrderService"],
                    "name": "cancelOrder",
                    "start_line": 40,
                    "end_line": 52,
                    "source_set": ":orders:main",
                    "indexed_at": "2026-03-20T00:00:00+00:00",
                    "retrieval_text": "cancel order refund",
                    "source_text": "public void cancelOrder(Long orderId) { paymentGateway.refund(); }",
                },
                {
                    "chunk_id": "chunk-2",
                    "file_path": "src/OrderService.java",
                    "chunk_kind": "method",
                    "package": "com.app.orders",
                    "owner_chain": ["OrderService"],
                    "name": "findOrder",
                    "start_line": 20,
                    "end_line": 28,
                    "source_set": ":orders:main",
                    "indexed_at": "2026-03-20T00:00:00+00:00",
                    "retrieval_text": "find order lookup",
                    "source_text": "public Order findOrder(Long orderId) { return orderRepo.findById(orderId); }",
                },
                {
                    "chunk_id": "chunk-3",
                    "file_path": "src/OtherService.java",
                    "chunk_kind": "method",
                    "package": "com.app.orders",
                    "owner_chain": ["OtherService"],
                    "name": "noop",
                    "start_line": 5,
                    "end_line": 7,
                    "source_set": ":orders:main",
                    "indexed_at": "2026-03-20T00:00:00+00:00",
                    "retrieval_text": "noop",
                    "source_text": "void noop() {}",
                },
            ],
            vectors=[
                [[1.0, 0.0]],
                [[0.2, 0.0]],
                [[0.1, 0.0]],
            ],
            model_name="ColBERT-Zero-supervised",
            indexed_at="2026-03-20T00:00:00+00:00",
        )

        service = RetrievalSearchService(repository, _FakeQueryEncoder())
        result = service.read_code("chunk-1")

    assert result is not None
    assert result.chunk_id == "chunk-1"
    assert result.symbol == "com.app.orders.OrderService.cancelOrder"
    assert result.file_path == "src/OrderService.java"
    assert result.start_line == 40
    assert result.surrounding_context["package_name"] == "com.app.orders"
    assert [neighbor.chunk_id for neighbor in result.neighbors] == ["chunk-2"]


def test_search_code_prefers_concrete_implementation_for_broad_lookup_queries(tmp_path):
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    class _BroadQueryEncoder:
        model_name = "fake-query-encoder"

        def embed_queries(self, texts):
            assert texts == ["where is bean lookup implemented"]
            return [[[1.0, 0.0]]]

    repository = FaissIndexRepository(tmp_path, faiss_module=_FakeFaiss())
    with patch(
        "codelens.indexing.faiss_repository.get_session", side_effect=_test_session
    ):
        repository.save(
            entries=[
                {
                    "chunk_id": "chunk-annotation",
                    "file_path": "src/Indexed.java",
                    "chunk_kind": "type",
                    "package": "io.micronaut.core.annotation",
                    "name": "Indexed",
                    "retrieval_text": "annotation bean lookup index",
                    "source_text": "@interface Indexed { }",
                },
                {
                    "chunk_id": "chunk-interface",
                    "file_path": "src/BeanResolver.java",
                    "chunk_kind": "type",
                    "package": "io.micronaut.context",
                    "name": "BeanResolver",
                    "retrieval_text": "interface bean lookup resolve bean",
                    "source_text": "public interface BeanResolver { Object getBean(); }",
                },
                {
                    "chunk_id": "chunk-default",
                    "file_path": "src/DefaultBeanContext.java",
                    "chunk_kind": "type",
                    "package": "io.micronaut.context",
                    "name": "DefaultBeanContext",
                    "retrieval_text": "class bean context lookup resolve bean getBean",
                    "source_text": (
                        "public class DefaultBeanContext { "
                        "Object getBean() { return null; } "
                        "}"
                    ),
                },
                {
                    "chunk_id": "chunk-method",
                    "file_path": "src/DefaultBeanContext.java",
                    "chunk_kind": "method",
                    "package": "io.micronaut.context",
                    "owner_chain": ["DefaultBeanContext"],
                    "name": "getBean",
                    "signature": "public <T> T getBean(Class<T> beanType)",
                    "retrieval_text": "method getBean resolve bean lookup bean registration",
                    "source_text": "public <T> T getBean(Class<T> beanType) { return resolveBeanRegistration().bean; }",
                },
            ],
            vectors=[
                [[1.0, 0.0]],
                [[1.0, 0.0]],
                [[1.0, 0.0]],
                [[1.0, 0.0]],
            ],
            model_name="ColBERT-Zero-supervised",
            indexed_at="2026-03-20T00:00:00+00:00",
        )

        service = RetrievalSearchService(repository, _BroadQueryEncoder())
        result = service.search_code(
            "where is bean lookup implemented",
            repo_root=str(tmp_path),
            top_k=3,
        )

    assert [hit.chunk_id for hit in result.results] == [
        "chunk-method",
        "chunk-default",
        "chunk-interface",
    ]
    assert "implementation:callable" in result.results[0].why_matched
    assert any(
        reason.startswith("lookup_terms:") for reason in result.results[0].why_matched
    )
    assert "implementation:concrete_type" in result.results[1].why_matched


def test_search_code_carries_line_metadata_from_parsed_java_through_to_hits(tmp_path):
    """End-to-end: parse Java → build IndexDocuments → store → search → verify line metadata."""
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    repo_root = tmp_path
    java_file = repo_root / "src" / "com" / "app" / "PaymentService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_bytes(b"""package com.app;

class PaymentService {
    private Gateway gateway;

    void processRefund(String orderId) {
        gateway.refund(orderId);
    }

    void chargeCustomer(String customerId) {
        gateway.charge(customerId);
    }
}
""")

    chunks = parse_java(java_file.read_bytes(), filepath=str(java_file))
    documents = build_index_documents(
        chunks,
        repo_root=repo_root,
        indexed_at="2026-03-28T00:00:00+00:00",
        source_set=":app:main",
    )

    method_docs = [d for d in documents if d.chunk_kind == "method"]
    assert len(method_docs) >= 2
    for doc in method_docs:
        assert doc.start_line is not None, f"{doc.name} missing start_line after build"
        assert doc.end_line is not None, f"{doc.name} missing end_line after build"

    entries = [document_payload(d) for d in documents]
    for entry in entries:
        if entry.get("chunk_kind") == "method":
            assert "start_line" in entry, (
                f"document_payload dropped start_line for {entry.get('name')}"
            )
            assert "end_line" in entry, (
                f"document_payload dropped end_line for {entry.get('name')}"
            )

    vectors = [[[1.0, 0.0]] for _ in entries]

    class _AnyQueryEncoder:
        model_name = "fake-query-encoder"

        def embed_queries(self, texts):
            return [[[1.0, 0.0]]]

    repository = FaissIndexRepository(tmp_path, faiss_module=_FakeFaiss())
    with patch(
        "codelens.indexing.faiss_repository.get_session", side_effect=_test_session
    ):
        repository.save(
            entries=entries,
            vectors=vectors,
            model_name="fake-model",
            indexed_at="2026-03-28T00:00:00+00:00",
        )

        service = RetrievalSearchService(repository, _AnyQueryEncoder())
        result = service.search_code(
            "processRefund",
            repo_root=str(tmp_path),
            top_k=10,
        )

    method_hits = [h for h in result.results if h.kind == "method"]
    assert len(method_hits) >= 1, "expected at least one method hit"
    for hit in method_hits:
        assert hit.start_line is not None, (
            f"search hit {hit.symbol} has start_line=None"
        )
        assert hit.end_line is not None, f"search hit {hit.symbol} has end_line=None"
        assert hit.start_line > 0
        assert hit.end_line >= hit.start_line
