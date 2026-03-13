from codelens.retrieval.repository import RetrievalDocumentRepository
from codelens.retrieval.service import RetrievalIndexingService
from codelens.storage.db import SQLiteConfig


def test_service_indexes_java_chunks_into_repository(tmp_path):
    repository = RetrievalDocumentRepository(SQLiteConfig.from_path(tmp_path / "retrieval.sqlite"))
    service = RetrievalIndexingService(repository)
    service.initialize()

    code = b"""package com.app.orders;

class OrderService {
    void placeOrder() {}
}
"""
    documents = service.index_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
        source_set=":orders:main",
    )

    stored = repository.list_documents(kind="method")
    assert len(stored) == 1
    assert stored[0].name == "placeOrder"
    assert stored[0].source_set == ":orders:main"
    assert stored[0].repo_root is None
    assert any(document.kind == "skeleton" for document in documents)
