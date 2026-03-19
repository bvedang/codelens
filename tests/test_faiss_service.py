import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

from codelens.indexing.encoder import LateInteractionEncoder
from codelens.indexing.faiss_repository import FaissIndexRepository
from codelens.indexing.service import FaissIndexingService, _workspace_signature


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


class _FakeEncoder(LateInteractionEncoder):
    @property
    def model_name(self) -> str:
        return "ColBERT-Zero-supervised"

    def embed_documents(self, texts):
        return [
            [[float(index + 1), float(index + 2)]]
            for index, _ in enumerate(texts)
        ]


class _SplittingEncoder(LateInteractionEncoder):
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    @property
    def model_name(self) -> str:
        return "ColBERT-Zero-supervised"

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        if len(texts) > 1:
            raise RuntimeError("MPS backend out of memory")
        return [[[1.0, 2.0]] for _ in texts]


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


def test_faiss_service_rebuilds_workspace_index(tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    (source_root / "OrderService.java").write_text(
        """package com.app;

class OrderService {
    void placeOrder() {
        System.out.println("ok");
    }
}
""",
        encoding="utf-8",
    )
    workspace_json = repo_root / "workspace.json"
    workspace_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sets": {
                    ":app:main": {
                        "source_roots": [str((repo_root / "app" / "src" / "main" / "java").resolve())],
                        "generated_source_roots": [],
                        "project_dependencies": [],
                        "external_jars": [],
                        "external_binary_entries": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    service = FaissIndexingService(repository, _FakeEncoder())

    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 2
    status = repository.status()
    assert status is not None
    assert status.chunk_count == result.documents_indexed
    assert status.model_name == "ColBERT-Zero-supervised"


def test_faiss_service_refreshes_single_file_by_rebuilding_retained_vectors(tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    keep_file = source_root / "Keep.java"
    refresh_file = source_root / "Refresh.java"
    keep_file.write_text(
        """package com.app;

class Keep {
    void keep() {
        System.out.println("keep");
    }
}
""",
        encoding="utf-8",
    )
    refresh_file.write_text(
        """package com.app;

class Refresh {
    void oldMethod() {
        System.out.println("old");
    }
}
""",
        encoding="utf-8",
    )
    workspace_json = repo_root / "workspace.json"
    workspace_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sets": {
                    ":app:main": {
                        "source_roots": [str((repo_root / "app" / "src" / "main" / "java").resolve())],
                        "generated_source_roots": [],
                        "project_dependencies": [],
                        "external_jars": [],
                        "external_binary_entries": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    service = FaissIndexingService(repository, _FakeEncoder())
    service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    refresh_file.write_text(
        """package com.app;

class Refresh {
    void newMethod() {
        System.out.println("new");
    }
}
""",
        encoding="utf-8",
    )

    result = service.index_file(
        refresh_file,
        repo_root=repo_root,
        workspace_json=workspace_json,
    )

    assert result.files_indexed == 1
    retained = repository.entries_with_vectors()
    chunk_ids = {payload["chunk_id"] for payload, _ in retained}
    names = {payload.get("name") for payload, _ in retained}
    assert "oldMethod" not in names
    assert "newMethod" in names
    assert "keep" in names
    assert chunk_ids


def test_faiss_service_resumes_workspace_build_from_checkpoint(tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    keep_file = source_root / "Keep.java"
    refresh_file = source_root / "Refresh.java"
    keep_file.write_text(
        """package com.app;

class Keep {
    void keep() {
        System.out.println("keep");
    }
}
""",
        encoding="utf-8",
    )
    refresh_file.write_text(
        """package com.app;

class Refresh {
    void refresh() {
        System.out.println("refresh");
    }
}
""",
        encoding="utf-8",
    )
    workspace_json = repo_root / "workspace.json"
    workspace_json.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sets": {
                    ":app:main": {
                        "source_roots": [str((repo_root / "app" / "src" / "main" / "java").resolve())],
                        "generated_source_roots": [],
                        "project_dependencies": [],
                        "external_jars": [],
                        "external_binary_entries": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    filepaths = [str(keep_file.resolve()), str(refresh_file.resolve())]
    state = repository.start_workspace_build(
        filepaths=filepaths,
        workspace_signature=_workspace_signature(filepaths),
        model_name="ColBERT-Zero-supervised",
        indexed_at="2026-03-10T00:00:00+00:00",
    )
    repository.append_workspace_file(
        workspace_file=str(keep_file.resolve()),
        entries=[
            {
                "chunk_id": "seed-chunk",
                "file_path": "app/src/main/java/com/app/Keep.java",
                "chunk_kind": "method",
                "retrieval_text": "[method] ...",
                "source_text": "void keep() { System.out.println(\"keep\"); }",
            }
        ],
        vectors=[[[0.1, 0.2]]],
    )
    assert state.status == "in_progress"

    service = FaissIndexingService(repository, _FakeEncoder())
    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 2
    assert result.documents_indexed >= 2
    workspace_state = repository.load_workspace_state()
    assert workspace_state is not None
    assert workspace_state.status == "completed"
    assert str(keep_file.resolve()) in workspace_state.completed_files
    assert str(refresh_file.resolve()) in workspace_state.completed_files


def test_faiss_service_splits_embedding_batches_on_out_of_memory(tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    java_file = source_root / "Large.java"
    java_file.write_text(
        """package com.app;

class Large {
    void alpha() {
        System.out.println("alpha");
    }

    void beta() {
        System.out.println("beta");
    }

    void gamma() {
        System.out.println("gamma");
    }
}
""",
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    encoder = _SplittingEncoder()
    service = FaissIndexingService(repository, encoder, batch_size=4)

    result = service.index_file(java_file, repo_root=repo_root)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 4
    assert any(batch_size > 1 for batch_size in encoder.batch_sizes)
    assert encoder.batch_sizes[-1] == 1
