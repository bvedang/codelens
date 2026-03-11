from codelens.retrieval.db import SQLiteConfig
from codelens.retrieval.documents import RetrievalDocument
from codelens.retrieval.repository import RetrievalDocumentRepository


def test_repository_round_trips_document(tmp_path):
    repository = RetrievalDocumentRepository(SQLiteConfig.from_path(tmp_path / "retrieval.sqlite"))
    repository.initialize()

    document = RetrievalDocument(
        chunk_id="chunk-1",
        kind="method",
        name="placeOrder",
        filepath="src/main/java/com/app/orders/OrderService.java",
        repo_root="/repo",
        package_name="com.app.orders",
        owner_chain=("OrderService",),
        source_set=":orders:main",
        signature="Order placeOrder(Customer customer)",
        return_type="Order",
        field_type=None,
        component_type=None,
        annotations=("@Transactional",),
        modifiers=("public",),
        calls=("com.app.payments.PaymentGateway.charge",),
        fields_accessed=("paymentGateway",),
        throws=(),
        extends_name=None,
        implements=(),
        permits=(),
        resolved_symbols=("com.app.payments.PaymentGateway.charge",),
        text="public Order placeOrder(Customer customer) { ... }",
        retrieval_text="method placeOrder PaymentGateway charge",
    )

    repository.upsert_documents([document])

    stored = repository.get_document("chunk-1")
    assert stored == document
    assert repository.list_documents(repo_root="/repo", kind="method") == [document]
    assert repository.list_documents(filepath=document.filepath) == [document]
    assert repository.list_documents(repo_root="/repo", filepath=document.filepath) == [document]


def test_repository_deletes_by_repo_and_file(tmp_path):
    repository = RetrievalDocumentRepository(SQLiteConfig.from_path(tmp_path / "retrieval.sqlite"))
    repository.initialize()

    first = RetrievalDocument(
        chunk_id="chunk-1",
        kind="method",
        name="placeOrder",
        filepath="src/main/java/com/app/orders/OrderService.java",
        repo_root="/repo",
        package_name="com.app.orders",
        owner_chain=("OrderService",),
        source_set=":orders:main",
        signature="Order placeOrder(Customer customer)",
        return_type="Order",
        field_type=None,
        component_type=None,
        annotations=(),
        modifiers=("public",),
        calls=(),
        fields_accessed=(),
        throws=(),
        extends_name=None,
        implements=(),
        permits=(),
        resolved_symbols=(),
        text="...",
        retrieval_text="...",
    )
    second = RetrievalDocument(
        chunk_id="chunk-2",
        kind="method",
        name="cancelOrder",
        filepath="src/main/java/com/app/orders/OrderService.java",
        repo_root="/repo",
        package_name="com.app.orders",
        owner_chain=("OrderService",),
        source_set=":orders:main",
        signature="void cancelOrder(Long orderId)",
        return_type="void",
        field_type=None,
        component_type=None,
        annotations=(),
        modifiers=("public",),
        calls=(),
        fields_accessed=(),
        throws=(),
        extends_name=None,
        implements=(),
        permits=(),
        resolved_symbols=(),
        text="...",
        retrieval_text="...",
    )

    repository.upsert_documents([first, second])
    repository.delete_by_file("/repo", "src/main/java/com/app/orders/OrderService.java")
    assert repository.list_documents(repo_root="/repo") == []
