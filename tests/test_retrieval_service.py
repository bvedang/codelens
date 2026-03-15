from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from codelens.repository.retrieval_document_repo import list_documents
from codelens.retrieval.service import RetrievalIndexingService


def test_service_indexes_java_chunks_into_repository(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    test_engine = create_engine(db_url)
    SQLModel.metadata.create_all(test_engine)

    with patch("codelens.retrieval.service.get_session") as mock_get_session:
        from contextlib import contextmanager

        @contextmanager
        def _test_session():
            with Session(test_engine) as session:
                yield session

        mock_get_session.side_effect = _test_session

        service = RetrievalIndexingService()

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

    with Session(test_engine) as session:
        stored = list_documents(session, kind="method", repo_root=None, filepath=None)

    assert len(stored) == 1
    assert stored[0].name == "placeOrder"
    assert stored[0].source_set == ":orders:main"
    assert stored[0].repo_root is None
    assert any(document.kind == "skeleton" for document in documents)
