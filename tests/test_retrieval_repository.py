import pytest
from sqlmodel import Session, SQLModel, create_engine

from codelens.models.retrieval_document import RetrievalDocument
from codelens.repository.retrieval_document_repo import (
    delete_by_file,
    delete_by_repo,
    get_document,
    list_documents,
    upsert_documents,
)


# --- Fixture ---
# We create an in-memory SQLite engine per test so each test is fully isolated
# and fast (no disk I/O, no temp files to clean up).
# create_all builds the table from the SQLModel metadata, replacing the old
# repository.initialize() call.
@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --- Helper ---
# The model has ~20 fields.  This factory fills in sensible defaults so each
# test only specifies the fields it cares about, keeping assertions readable.
def _make_document(**overrides) -> RetrievalDocument:
    defaults = dict(
        chunk_id="chunk-1",
        kind="method",
        name="placeOrder",
        filepath="src/main/java/com/app/orders/OrderService.java",
        repo_root="/repo",
        package_name="com.app.orders",
        owner_chain=["OrderService"],
        source_set=":orders:main",
        signature="Order placeOrder(Customer customer)",
        return_type="Order",
        annotations=["@Transactional"],
        modifiers=["public"],
        calls=["com.app.payments.PaymentGateway.charge"],
        fields_accessed=["paymentGateway"],
        resolved_symbols=["com.app.payments.PaymentGateway.charge"],
        text="public Order placeOrder(Customer customer) { ... }",
        retrieval_text="method placeOrder PaymentGateway charge",
    )
    defaults.update(overrides)
    return RetrievalDocument(**defaults)  # type: ignore[arg-type]


# --- Tests ---


def test_upsert_and_get(session):
    """Round-trip: insert a document, read it back by primary key."""
    doc = _make_document()
    upsert_documents(session, [doc])

    stored = get_document(session, "chunk-1")

    assert stored is not None
    assert stored.chunk_id == "chunk-1"
    assert stored.name == "placeOrder"
    assert stored.source_set == ":orders:main"
    assert stored.annotations == ["@Transactional"]


def test_list_filters(session):
    """list_documents respects repo_root, kind, and filepath filters."""
    method = _make_document(chunk_id="c-1", kind="method")
    skeleton = _make_document(chunk_id="c-2", kind="skeleton", name="OrderService")
    upsert_documents(session, [method, skeleton])

    by_kind = list_documents(session, repo_root=None, kind="method", filepath=None)
    assert len(by_kind) == 1
    assert by_kind[0].chunk_id == "c-1"

    by_repo = list_documents(session, repo_root="/repo", kind=None, filepath=None)
    assert len(by_repo) == 2

    by_file = list_documents(
        session,
        repo_root=None,
        kind=None,
        filepath="src/main/java/com/app/orders/OrderService.java",
    )
    assert len(by_file) == 2


def test_delete_by_file(session):
    """delete_by_file removes only documents matching repo + filepath."""
    doc_a = _make_document(chunk_id="c-1", filepath="A.java")
    doc_b = _make_document(chunk_id="c-2", filepath="B.java")
    upsert_documents(session, [doc_a, doc_b])

    delete_by_file(session, "/repo", "A.java")

    remaining = list_documents(session, repo_root="/repo", kind=None, filepath=None)
    assert len(remaining) == 1
    assert remaining[0].chunk_id == "c-2"


def test_delete_by_repo(session):
    """delete_by_repo removes all documents for that repo_root."""
    doc_a = _make_document(chunk_id="c-1")
    doc_b = _make_document(chunk_id="c-2", name="cancelOrder")
    upsert_documents(session, [doc_a, doc_b])

    delete_by_repo(session, "/repo")

    assert list_documents(session, repo_root="/repo", kind=None, filepath=None) == []


def test_upsert_updates_existing(session):
    """merge() should update an existing row rather than raise on duplicate PK."""
    original = _make_document(name="placeOrder")
    upsert_documents(session, [original])

    updated = _make_document(name="placeOrderV2")
    upsert_documents(session, [updated])

    stored = get_document(session, "chunk-1")
    assert stored is not None
    assert stored.name == "placeOrderV2"
