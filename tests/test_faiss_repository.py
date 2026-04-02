from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from codelens.indexing.faiss_repository import (
    FaissIndexRepository,
    _prepare_query_matrix,
)


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


@pytest.fixture(autouse=True)
def _db():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _test_session():
        with Session(engine) as session:
            yield session

    with patch(
        "codelens.indexing.faiss_repository.get_session", side_effect=_test_session
    ):
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
    keep_file = str((tmp_path / "src" / "Keep.java").resolve())
    refresh_file = str((tmp_path / "src" / "Refresh.java").resolve())
    Path(keep_file).parent.mkdir(parents=True, exist_ok=True)
    Path(keep_file).write_text("class Keep {}", encoding="utf-8")
    Path(refresh_file).write_text("class Refresh {}", encoding="utf-8")

    state = repository.start_workspace_build(
        filepaths=[keep_file, refresh_file],
        workspace_signature="workspace-v1",
        model_name="lightonai/ColBERT-Zero",
        indexed_at="2026-03-10T00:00:00+00:00",
    )
    assert state.status == "in_progress"

    state = repository.append_workspace_file(
        workspace_file=keep_file,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/Keep.java:method:keep",
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
    assert state.completed_files == [keep_file]
    assert state.documents_indexed == 1

    resumed = repository.start_workspace_build(
        filepaths=[keep_file, refresh_file],
        workspace_signature="workspace-v1",
        model_name="lightonai/ColBERT-Zero",
        indexed_at="2026-03-10T00:00:01+00:00",
    )
    assert resumed.completed_files == [keep_file]
    assert resumed.documents_indexed == 1

    final_state = repository.complete_workspace_build()
    assert final_state.status == "completed"

    loaded = repository.load()
    assert loaded is not None
    chunk_id = f"{repository._repo_hash}:src/Keep.java:method:keep"
    assert loaded.chunks[chunk_id].shard == "00000000.faiss"
    retained = repository.entries_with_vectors()
    assert retained[0][1][0] == pytest.approx([0.1, 0.2])


def test_faiss_repository_retries_interrupted_workspace_file_without_duplicate_chunks(
    tmp_path,
):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)
    keep_file = str((tmp_path / "src" / "Keep.java").resolve())
    Path(keep_file).parent.mkdir(parents=True, exist_ok=True)
    Path(keep_file).write_text("class Keep {}", encoding="utf-8")

    repository.start_workspace_build(
        filepaths=[keep_file],
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
                workspace_file=keep_file,
                entries=entries,
                vectors=vectors,
            )

    state = repository.append_workspace_file(
        workspace_file=keep_file,
        entries=entries,
        vectors=vectors,
    )

    assert state.completed_files == [keep_file]
    loaded = repository.load()
    assert loaded is not None
    assert list(loaded.chunks) == [f"{repository._repo_hash}:src/Keep.java:method:keep"]
    retained = repository.entries_with_vectors()
    assert retained[0][0] == entries[0]
    assert retained[0][1][0] == pytest.approx([0.1, 0.2])


def test_faiss_repository_search_shortlists_candidates_before_exact_scoring(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    entries = []
    vectors = []
    for index in range(150):
        entries.append(
            {
                "chunk_id": f"chunk-{index:03d}",
                "file_path": f"src/File{index:03d}.java",
                "chunk_kind": "method",
                "retrieval_text": f"method {index}",
                "source_text": f"void method{index}() {{}}",
            }
        )
        vectors.append([[150.0 - index, 0.0]])

    repository.save(
        entries=entries,
        vectors=vectors,
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    reconstruct_calls = 0
    original_reconstruct = repository._reconstruct_vectors

    def _counting_reconstruct(**kwargs):
        nonlocal reconstruct_calls
        reconstruct_calls += 1
        return original_reconstruct(**kwargs)

    with patch.object(
        repository, "_reconstruct_vectors", side_effect=_counting_reconstruct
    ):
        results = repository.search([[1.0, 0.0]], top_k=1)

    assert results[0][0]["chunk_id"] == "chunk-000"
    assert reconstruct_calls == 32


def test_prepare_query_matrix_sanitizes_non_finite_values():
    matrix = _prepare_query_matrix([[1.0, float("nan")], [float("inf"), -2.0]])

    assert matrix.dtype.name == "float32"
    assert matrix.flags["C_CONTIGUOUS"] is True
    assert matrix.tolist() == [[1.0, 0.0], [0.0, -2.0]]


def test_append_workspace_file_replaces_existing_file_chunks_without_duplicates(
    tmp_path,
):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)
    workspace_file = str((tmp_path / "src" / "Refresh.java").resolve())
    Path(workspace_file).parent.mkdir(parents=True, exist_ok=True)
    Path(workspace_file).write_text("class Refresh {}", encoding="utf-8")

    repository.start_workspace_build(
        filepaths=[workspace_file],
        workspace_signature="sig",
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-23T00:00:00+00:00",
    )
    repository.append_workspace_file(
        workspace_file=workspace_file,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/Refresh.java:method:oldMethod",
                "file_path": "src/Refresh.java",
                "chunk_kind": "method",
                "name": "oldMethod",
                "retrieval_text": "old method",
                "source_text": "void oldMethod() {}",
            }
        ],
        vectors=[[[1.0, 0.0]]],
    )

    state = repository.append_workspace_file(
        workspace_file=workspace_file,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/Refresh.java:method:newMethod",
                "file_path": "src/Refresh.java",
                "chunk_kind": "method",
                "name": "newMethod",
                "retrieval_text": "new method",
                "source_text": "void newMethod() {}",
            }
        ],
        vectors=[[[0.5, 0.0]]],
    )

    retained = repository.entries_with_vectors()
    chunk_ids = [payload["chunk_id"] for payload, _ in retained]
    names = [payload.get("name") for payload, _ in retained]

    assert state.documents_indexed == 1
    assert chunk_ids == [f"{repository._repo_hash}:src/Refresh.java:method:newMethod"]
    assert names == ["newMethod"]


def test_append_workspace_file_removes_stale_chunks_when_rebuild_is_empty(tmp_path):
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)
    workspace_file = str((tmp_path / "src" / "Refresh.java").resolve())
    Path(workspace_file).parent.mkdir(parents=True, exist_ok=True)
    Path(workspace_file).write_text("class Refresh {}", encoding="utf-8")

    repository.start_workspace_build(
        filepaths=[workspace_file],
        workspace_signature="sig",
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-23T00:00:00+00:00",
    )
    repository.append_workspace_file(
        workspace_file=workspace_file,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/Refresh.java:method:oldMethod",
                "file_path": "src/Refresh.java",
                "chunk_kind": "method",
                "name": "oldMethod",
                "retrieval_text": "old method",
                "source_text": "void oldMethod() {}",
            }
        ],
        vectors=[[[1.0, 0.0]]],
    )

    state = repository.append_workspace_file(
        workspace_file=workspace_file,
        entries=[],
        vectors=[],
    )

    assert state.documents_indexed == 0
    assert repository.entries_with_vectors() == []


def test_workspace_built_index_is_searchable_across_shards(tmp_path):
    """After a workspace build with multiple files (one shard each), search
    must find results from every shard and reconstruct their vectors."""
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    file_a = str((tmp_path / "src" / "A.java").resolve())
    file_b = str((tmp_path / "src" / "B.java").resolve())
    Path(file_a).parent.mkdir(parents=True, exist_ok=True)
    Path(file_a).write_text("class A {}", encoding="utf-8")
    Path(file_b).write_text("class B {}", encoding="utf-8")

    repository.start_workspace_build(
        filepaths=[file_a, file_b],
        workspace_signature="sig",
        model_name="test-model",
        indexed_at="2026-03-28T00:00:00+00:00",
    )
    repository.append_workspace_file(
        workspace_file=file_a,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/A.java:method:alpha",
                "file_path": "src/A.java",
                "chunk_kind": "method",
                "name": "alpha",
                "start_line": 5,
                "end_line": 10,
                "retrieval_text": "alpha method",
                "source_text": "void alpha() { System.out.println(1); }",
            },
        ],
        vectors=[[[1.0, 0.0]]],
    )
    repository.append_workspace_file(
        workspace_file=file_b,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/B.java:method:beta",
                "file_path": "src/B.java",
                "chunk_kind": "method",
                "name": "beta",
                "start_line": 12,
                "end_line": 18,
                "retrieval_text": "beta method",
                "source_text": "void beta() { System.out.println(2); }",
            },
        ],
        vectors=[[[0.8, 0.0]]],
    )
    repository.complete_workspace_build()

    results = repository.search([[1.0, 0.0]], top_k=5)

    result_ids = [payload["chunk_id"] for payload, _ in results]
    assert f"{repository._repo_hash}:src/A.java:method:alpha" in result_ids
    assert f"{repository._repo_hash}:src/B.java:method:beta" in result_ids

    for payload, score in results:
        assert score > 0
        assert payload.get("start_line") is not None, (
            f"chunk {payload['chunk_id']} lost start_line after workspace build"
        )


def test_workspace_reindex_file_produces_searchable_results(tmp_path):
    """After reindexing a single file during a workspace build, search must
    return the new data and must not return stale chunks or hit reconstruct
    errors from the old shard."""
    fake_faiss = _FakeFaiss()
    repository = FaissIndexRepository(tmp_path, faiss_module=fake_faiss)

    file_a = str((tmp_path / "src" / "A.java").resolve())
    file_b = str((tmp_path / "src" / "B.java").resolve())
    Path(file_a).parent.mkdir(parents=True, exist_ok=True)
    Path(file_a).write_text("class A {}", encoding="utf-8")
    Path(file_b).write_text("class B {}", encoding="utf-8")

    repository.start_workspace_build(
        filepaths=[file_a, file_b],
        workspace_signature="sig",
        model_name="test-model",
        indexed_at="2026-03-28T00:00:00+00:00",
    )
    repository.append_workspace_file(
        workspace_file=file_a,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/A.java:method:oldMethod",
                "file_path": "src/A.java",
                "chunk_kind": "method",
                "name": "oldMethod",
                "start_line": 5,
                "end_line": 10,
                "retrieval_text": "old method",
                "source_text": "void oldMethod() { System.out.println(1); }",
            },
        ],
        vectors=[[[0.9, 0.0]]],
    )
    repository.append_workspace_file(
        workspace_file=file_b,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/B.java:method:beta",
                "file_path": "src/B.java",
                "chunk_kind": "method",
                "name": "beta",
                "start_line": 3,
                "end_line": 8,
                "retrieval_text": "beta method",
                "source_text": "void beta() { System.out.println(2); }",
            },
        ],
        vectors=[[[0.5, 0.0]]],
    )

    # Reindex file A with different content and a different number of vectors.
    repository.append_workspace_file(
        workspace_file=file_a,
        entries=[
            {
                "chunk_id": f"{repository._repo_hash}:src/A.java:method:newAlpha",
                "file_path": "src/A.java",
                "chunk_kind": "method",
                "name": "newAlpha",
                "start_line": 5,
                "end_line": 15,
                "retrieval_text": "new alpha method",
                "source_text": "void newAlpha() { System.out.println(3); }",
            },
            {
                "chunk_id": f"{repository._repo_hash}:src/A.java:method:newGamma",
                "file_path": "src/A.java",
                "chunk_kind": "method",
                "name": "newGamma",
                "start_line": 17,
                "end_line": 22,
                "retrieval_text": "new gamma method",
                "source_text": "void newGamma() { System.out.println(4); }",
            },
        ],
        vectors=[[[1.0, 0.0]], [[0.7, 0.0]]],
    )
    repository.complete_workspace_build()

    results = repository.search([[1.0, 0.0]], top_k=10)
    result_names = [payload.get("name") for payload, _ in results]

    assert "oldMethod" not in result_names, "stale chunk survived reindex"
    assert "newAlpha" in result_names
    assert "newGamma" in result_names
    assert "beta" in result_names

    for payload, score in results:
        assert score > 0
        assert payload.get("start_line") is not None
