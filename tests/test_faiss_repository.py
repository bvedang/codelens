from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from codelens.indexing.faiss_repository import FaissIndexRepository


class _FakeIndex:
    def __init__(self, vector_size):
        self.d = vector_size
        self.vectors = []

    def add(self, rows):
        for row in rows.tolist():
            self.vectors.append(list(row))

    def reconstruct(self, index):
        return self.vectors[index]


class _FakeFaiss:
    def __init__(self):
        self.saved = {}

    def IndexFlatIP(self, vector_size):
        return _FakeIndex(vector_size)

    def write_index(self, index, path):
        self.saved[path] = index

    def read_index(self, path):
        return self.saved[path]


@pytest.fixture(autouse=True)
def _db():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    with patch("codelens.indexing.faiss_repository.get_session", side_effect=_test_session):
        yield


def test_faiss_repository_persists_metadata_and_vectors(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    repository.save(
        entries=[
            {
                "chunk_id": "chunk-1",
                "file_path": "src/OrderService.java",
                "chunk_kind": "method",
                "retrieval_text": "[method] ...",
                "source_text": "void placeOrder() { paymentGateway.charge(); }",
            },
            {
                "chunk_id": "chunk-2",
                "file_path": "src/OrderService.java",
                "chunk_kind": "field",
                "retrieval_text": "[field] ...",
                "source_text": "PaymentGateway paymentGateway;",
            },
        ],
        vectors=[
            [[0.1, 0.2], [0.3, 0.4]],
            [[0.5, 0.6]],
        ],
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    loaded = repository.load()
    assert loaded is not None
    assert loaded.model_name == "ColBERT-Zero-supervised"
    assert loaded.chunks["chunk-1"].faiss_ids == (0, 1)
    assert loaded.chunks["chunk-2"].faiss_ids == (2,)

    retained = repository.entries_with_vectors()
    assert retained[0][1][0] == pytest.approx([0.1, 0.2])
    assert retained[0][1][1] == pytest.approx([0.3, 0.4])
    assert retained[1][1][0] == pytest.approx([0.5, 0.6])


def test_faiss_repository_filters_entries_by_file_path(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    repository.save(
        entries=[
            {
                "chunk_id": "chunk-1",
                "file_path": "src/Keep.java",
                "chunk_kind": "method",
                "retrieval_text": "[method] ...",
                "source_text": "void keep() { System.out.println(1); }",
            },
            {
                "chunk_id": "chunk-2",
                "file_path": "src/Refresh.java",
                "chunk_kind": "method",
                "retrieval_text": "[method] ...",
                "source_text": "void refresh() { System.out.println(2); }",
            },
        ],
        vectors=[
            [[0.1, 0.2]],
            [[0.3, 0.4]],
        ],
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    retained = repository.entries_with_vectors(exclude_file_path="src/Refresh.java")
    assert [payload["chunk_id"] for payload, _ in retained] == ["chunk-1"]


def test_faiss_repository_checkpoints_workspace_shards_and_resume_state(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    state = repository.start_workspace_build(
        filepaths=["/repo/src/Keep.java", "/repo/src/Refresh.java"],
        workspace_signature="workspace-v1",
        model_name="lightonai/ColBERT-Zero",
        indexed_at="2026-03-10T00:00:00+00:00",
    )
    assert state.status == "in_progress"

    state = repository.append_workspace_file(
        workspace_file="/repo/src/Keep.java",
        entries=[
            {
                "chunk_id": "chunk-1",
                "file_path": "src/Keep.java",
                "chunk_kind": "method",
                "retrieval_text": "[method] ...",
                "source_text": "void keep() { System.out.println(1); }",
            }
        ],
        vectors=[
            [[0.1, 0.2]],
        ],
    )
    assert state.completed_files == ["/repo/src/Keep.java"]
    assert state.documents_indexed == 1

    resumed = repository.start_workspace_build(
        filepaths=["/repo/src/Keep.java", "/repo/src/Refresh.java"],
        workspace_signature="workspace-v1",
        model_name="lightonai/ColBERT-Zero",
        indexed_at="2026-03-10T00:00:01+00:00",
    )
    assert resumed.completed_files == ["/repo/src/Keep.java"]
    assert resumed.documents_indexed == 1

    final_state = repository.complete_workspace_build()
    assert final_state.status == "completed"

    loaded = repository.load()
    assert loaded is not None
    assert loaded.chunks["chunk-1"].shard == "00000000.faiss"
    retained = repository.entries_with_vectors()
    assert retained[0][1][0] == pytest.approx([0.1, 0.2])


def test_faiss_repository_retries_interrupted_workspace_file_without_duplicate_chunks(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    repository.start_workspace_build(
        filepaths=["/repo/src/Keep.java"],
        workspace_signature="workspace-v1",
        model_name="lightonai/ColBERT-Zero",
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    entries = [
        {
            "chunk_id": f"{repository._repo_hash}:src/Keep.java:method:keep",
            "file_path": "src/Keep.java",
            "chunk_kind": "method",
            "retrieval_text": "[method] ...",
            "source_text": "void keep() { System.out.println(1); }",
        }
    ]
    vectors = [[[0.1, 0.2]]]

    with patch.object(repository, "_write_state", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            repository.append_workspace_file(
                workspace_file="/repo/src/Keep.java",
                entries=entries,
                vectors=vectors,
            )

    state = repository.append_workspace_file(
        workspace_file="/repo/src/Keep.java",
        entries=entries,
        vectors=vectors,
    )

    assert state.completed_files == ["/repo/src/Keep.java"]
    loaded = repository.load()
    assert loaded is not None
    assert list(loaded.chunks) == [f"{repository._repo_hash}:src/Keep.java:method:keep"]
    retained = repository.entries_with_vectors()
    assert retained[0][0] == entries[0]
    assert retained[0][1][0] == pytest.approx([0.1, 0.2])
