from pathlib import Path

from codelens.retrieval.qdrant import QdrantConfig, QdrantDocumentRepository
from codelens.retrieval.qdrant_service import QdrantIndexingService


class _FakeEncoder:
    def embed_documents(self, texts):
        return [
            [[float(index + 1), float(index + 2)]]
            for index, _ in enumerate(texts)
        ]


class _FakeRepository(QdrantDocumentRepository):
    def __init__(self):
        super().__init__(QdrantConfig(collection_name="test_chunks"), client=object())
        self.deleted_repo = []
        self.deleted_file = []
        self.ensure_sizes = []
        self.upserts = []

    def ensure_collection(self, vector_size: int) -> None:
        self.ensure_sizes.append(vector_size)

    def upsert_documents(self, documents, *, vectors) -> None:
        self.upserts.append((documents, vectors))

    def delete_by_repo(self, repo_root: str) -> None:
        self.deleted_repo.append(repo_root)

    def delete_by_file(self, repo_root: str, filepath: str) -> None:
        self.deleted_file.append((repo_root, filepath))


def test_qdrant_service_indexes_single_file_without_workspace(tmp_path):
    java_file = tmp_path / "src" / "main" / "java" / "com" / "app" / "OrderService.java"
    java_file.parent.mkdir(parents=True)
    java_file.write_text(
        "package com.app; class OrderService { void placeOrder() {} }",
        encoding="utf-8",
    )
    repository = _FakeRepository()
    service = QdrantIndexingService(repository, _FakeEncoder())

    result = service.index_file(java_file, repo_root=tmp_path)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 2
    assert repository.deleted_file == [(str(tmp_path.resolve()), str(java_file.resolve()))]
    assert repository.ensure_sizes == [2]


def test_qdrant_service_rebuilds_workspace_index(tmp_path):
    repo_root = tmp_path / "repo"
    source_root = repo_root / "orders" / "src" / "main" / "java" / "com" / "app"
    source_root.mkdir(parents=True)
    (source_root / "OrderService.java").write_text(
        "package com.app; class OrderService { void placeOrder() {} }",
        encoding="utf-8",
    )
    workspace_json = repo_root / "workspace.json"
    workspace_json.write_text(
        """
{
  "schema_version": 1,
  "source_sets": {
    ":orders:main": {
      "source_roots": ["/REPO/orders/src/main/java"],
      "generated_source_roots": [],
      "project_dependencies": [],
      "external_jars": [],
      "external_binary_entries": []
    }
  }
}
        """.replace("/REPO", str(repo_root.resolve())),
        encoding="utf-8",
    )

    repository = _FakeRepository()
    service = QdrantIndexingService(repository, _FakeEncoder())

    result = service.index_workspace(repo_root=repo_root, workspace_json=workspace_json)

    assert result.files_indexed == 1
    assert result.documents_indexed >= 2
    assert repository.deleted_repo == [str(repo_root.resolve())]
