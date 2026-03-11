from types import SimpleNamespace

from codelens.retrieval.documents import RetrievalDocument
from codelens.retrieval.qdrant import QdrantConfig, QdrantDocumentRepository


class _FakeModels:
    class Distance:
        COSINE = "cosine"

    class MultiVectorComparator:
        MAX_SIM = "max_sim"

    class MultiVectorConfig:
        def __init__(self, comparator):
            self.comparator = comparator

    class VectorParams:
        def __init__(self, size, distance, multivector_config):
            self.size = size
            self.distance = distance
            self.multivector_config = multivector_config

    class PointStruct:
        def __init__(self, id, vector, payload):
            self.id = id
            self.vector = vector
            self.payload = payload

    class MatchValue:
        def __init__(self, value):
            self.value = value

    class FieldCondition:
        def __init__(self, key, match):
            self.key = key
            self.match = match

    class Filter:
        def __init__(self, must):
            self.must = must

    class FilterSelector:
        def __init__(self, filter):
            self.filter = filter


class _FakeClient:
    def __init__(self):
        self.created = []
        self.upserts = []
        self.deletes = []
        self._exists = False

    def collection_exists(self, name):
        return self._exists

    def create_collection(self, **kwargs):
        self._exists = True
        self.created.append(kwargs)

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)


class _TestRepository(QdrantDocumentRepository):
    def __init__(self, config, client):
        super().__init__(config, client=client)

    def _models(self):
        return _FakeModels


def test_qdrant_repository_creates_collection_and_upserts_points():
    client = _FakeClient()
    repository = _TestRepository(QdrantConfig(collection_name="test_chunks"), client)
    document = RetrievalDocument(
        chunk_id="chunk-1",
        kind="method",
        name="placeOrder",
        filepath="/repo/src/OrderService.java",
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

    repository.ensure_collection(128)
    repository.upsert_documents([document], vectors=[[[0.1, 0.2], [0.3, 0.4]]])

    assert client.created[0]["collection_name"] == "test_chunks"
    upsert = client.upserts[0]
    assert upsert["collection_name"] == "test_chunks"
    assert upsert["points"][0].payload["repo_root"] == "/repo"


def test_qdrant_repository_deletes_by_repo_and_file():
    client = _FakeClient()
    repository = _TestRepository(QdrantConfig(collection_name="test_chunks"), client)

    repository.delete_by_repo("/repo")
    repository.delete_by_file("/repo", "/repo/src/OrderService.java")

    assert len(client.deletes) == 2
    repo_delete = client.deletes[0]["points_selector"].filter.must
    file_delete = client.deletes[1]["points_selector"].filter.must
    assert repo_delete[0].key == "repo_root"
    assert file_delete[1].key == "filepath"
