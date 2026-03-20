import json
import logging
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

import codelens.workspace_runtime as workspace_runtime
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
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("fake-faiss-index", encoding="utf-8")

    def read_index(self, path):
        return self.saved[path]


class _FakeEncoder(LateInteractionEncoder):
    def __init__(self) -> None:
        self.prepare_calls = 0

    @property
    def model_name(self) -> str:
        return "ColBERT-Zero-supervised"

    def prepare(self) -> None:
        self.prepare_calls += 1

    def embed_documents(self, texts):
        return [
            [[float(index + 1), float(index + 2)]]
            for index, _ in enumerate(texts)
        ]


class _SplittingEncoder(LateInteractionEncoder):
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.prepare_calls = 0

    @property
    def model_name(self) -> str:
        return "ColBERT-Zero-supervised"

    def prepare(self) -> None:
        self.prepare_calls += 1

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        if len(texts) > 1:
            raise RuntimeError("MPS backend out of memory")
        return [[[1.0, 2.0]] for _ in texts]


class _BufferSizeFailingEncoder(LateInteractionEncoder):
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.prepare_calls = 0

    @property
    def model_name(self) -> str:
        return "ColBERT-Zero-supervised"

    def prepare(self) -> None:
        self.prepare_calls += 1

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        if len(texts) > 1:
            raise RuntimeError("Invalid buffer size: 48.00 GiB")
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
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 2
    assert encoder.prepare_calls == 1
    status = repository.status()
    assert status is not None
    assert status.chunk_count == result.documents_indexed
    assert status.model_name == "ColBERT-Zero-supervised"


def test_faiss_service_logs_workspace_progress(tmp_path, caplog):
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
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    with caplog.at_level(logging.INFO, logger="codelens.indexing.service"):
        service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    messages = [record.message for record in caplog.records]
    assert any("Preparing workspace index" in message for message in messages)
    assert any("Preloading encoder model" in message for message in messages)
    assert any("Workspace indexing started" in message for message in messages)
    assert any("Prepared workspace shared context" in message for message in messages)
    assert any("Prepared workspace resolver context" in message for message in messages)
    assert any("Indexed workspace file" in message for message in messages)
    assert any("Workspace indexing finished" in message for message in messages)
    assert any(
        "Prepared workspace shared context |" in message
        and "duration_ms=" in message
        for message in messages
    )
    assert any(
        "Prepared workspace resolver context |" in message
        and "duration_ms=" in message
        for message in messages
    )
    assert any(
        "Indexed workspace file |" in message
        and "parse_ms=" in message
        and "embed_ms=" in message
        and "persist_ms=" in message
        for message in messages
    )
    assert any(
        "Workspace indexing finished |" in message
        and "parse_ms=" in message
        and "embed_ms=" in message
        and "persist_ms=" in message
        and "total_ms=" in message
        for message in messages
    )
    assert encoder.prepare_calls == 1


def test_faiss_service_logs_single_file_timing_fields(tmp_path, caplog):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    java_file = source_root / "OrderService.java"
    java_file.write_text(
        """package com.app;

class OrderService {
    void placeOrder() {
        System.out.println("ok");
    }
}
""",
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    with caplog.at_level(logging.INFO, logger="codelens.indexing.service"):
        service.index_file(java_file, repo_root=repo_root)

    messages = [record.message for record in caplog.records]
    assert any(
        "Indexed file timings |" in message
        and "parse_ms=" in message
        and "embed_ms=" in message
        and "persist_ms=" in message
        for message in messages
    )
    assert encoder.prepare_calls == 1


def test_faiss_service_indexes_main_source_sets_only_by_default(tmp_path, caplog):
    repo_root = tmp_path / "repo"
    main_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    test_root = repo_root / "app" / "src" / "test" / "java" / "com" / "app"
    main_root.mkdir(parents=True)
    test_root.mkdir(parents=True)
    (main_root / "MainOnly.java").write_text(
        """package com.app;

class MainOnly {
    void production() {
        System.out.println("prod");
    }
}
""",
        encoding="utf-8",
    )
    (test_root / "MainOnlyTest.java").write_text(
        """package com.app;

class MainOnlyTest {
    void spec() {
        System.out.println("test");
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
                    },
                    ":app:test": {
                        "source_roots": [str((repo_root / "app" / "src" / "test" / "java").resolve())],
                        "generated_source_roots": [],
                        "project_dependencies": [],
                        "external_jars": [],
                        "external_binary_entries": [],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    with caplog.at_level(logging.INFO):
        result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 1
    retained = repository.entries_with_vectors()
    names = {payload.get("name") for payload, _ in retained}
    file_paths = {payload.get("file_path") for payload, _ in retained}
    assert "production" in names
    assert "spec" not in names
    assert "app/src/main/java/com/app/MainOnly.java" in file_paths
    assert "app/src/test/java/com/app/MainOnlyTest.java" not in file_paths

    messages = [record.message for record in caplog.records]
    assert any(
        "Preparing workspace index |" in message
        and "scope=main-only" in message
        and "deferred_source_sets=test" in message
        and "deferred_files=1" in message
        for message in messages
    )
    assert any("Building workspace-wide source index for 1 roots" in message for message in messages)


def test_faiss_service_reuses_workspace_context_per_source_set(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    for name in ("Alpha.java", "Beta.java"):
        (source_root / name).write_text(
            f"""package com.app;

class {name[:-5]} {{
    void run() {{
        System.out.println("ok");
    }}
}}
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

    build_calls: list[str | None] = []
    shared_calls = 0
    original = workspace_runtime.build_workspace_source_set_context
    original_shared = workspace_runtime.build_workspace_shared_context

    def _record_build(source_set_id, **kwargs):
        build_calls.append(source_set_id.key if source_set_id is not None else None)
        return original(source_set_id, **kwargs)

    def _record_shared(**kwargs):
        nonlocal shared_calls
        shared_calls += 1
        return original_shared(**kwargs)

    monkeypatch.setattr(workspace_runtime, "build_workspace_source_set_context", _record_build)
    monkeypatch.setattr(workspace_runtime, "build_workspace_shared_context", _record_shared)

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 2
    assert shared_calls == 1
    assert build_calls == [":app:main"]


def test_faiss_service_can_index_test_file_on_demand_after_main_bootstrap(tmp_path):
    repo_root = tmp_path / "repo"
    main_root = repo_root / "app" / "src" / "main" / "java" / "com" / "app"
    test_root = repo_root / "app" / "src" / "test" / "java" / "com" / "app"
    main_root.mkdir(parents=True)
    test_root.mkdir(parents=True)
    main_file = main_root / "MainOnly.java"
    test_file = test_root / "MainOnlyTest.java"
    main_file.write_text(
        """package com.app;

class MainOnly {
    void production() {
        System.out.println("prod");
    }
}
""",
        encoding="utf-8",
    )
    test_file.write_text(
        """package com.app;

class MainOnlyTest {
    void spec() {
        System.out.println("test");
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
                    },
                    ":app:test": {
                        "source_roots": [str((repo_root / "app" / "src" / "test" / "java").resolve())],
                        "generated_source_roots": [],
                        "project_dependencies": [],
                        "external_jars": [],
                        "external_binary_entries": [],
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    repository = FaissIndexRepository(repo_root, faiss_module=_FakeFaiss())
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)

    workspace_result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)
    file_result = service.index_file(
        test_file,
        repo_root=repo_root,
        workspace_json=workspace_json,
    )

    assert workspace_result.files_indexed == 1
    assert file_result.files_indexed == 1
    retained = repository.entries_with_vectors()
    names = {payload.get("name") for payload, _ in retained}
    file_paths = {payload.get("file_path") for payload, _ in retained}
    assert "production" in names
    assert "spec" in names
    assert "app/src/main/java/com/app/MainOnly.java" in file_paths
    assert "app/src/test/java/com/app/MainOnlyTest.java" in file_paths


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
    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)
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
    assert encoder.prepare_calls == 2
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

    encoder = _FakeEncoder()
    service = FaissIndexingService(repository, encoder)
    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 2
    assert result.documents_indexed >= 2
    assert encoder.prepare_calls == 1
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
    assert encoder.prepare_calls == 1
    assert any(batch_size > 1 for batch_size in encoder.batch_sizes)
    assert encoder.batch_sizes[-1] == 1


def test_faiss_service_splits_embedding_batches_on_invalid_buffer_size(tmp_path):
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
    encoder = _BufferSizeFailingEncoder()
    service = FaissIndexingService(repository, encoder, batch_size=4)

    result = service.index_file(java_file, repo_root=repo_root)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 4
    assert encoder.prepare_calls == 1
    assert any(batch_size > 1 for batch_size in encoder.batch_sizes)
    assert encoder.batch_sizes[-1] == 1
