from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codelens.chunker import parse_java
from codelens.gradle_model import GradleWorkspaceModel
from codelens.logging_config import get_logger
from codelens.retrieval.documents import RetrievalDocument, build_retrieval_documents
from codelens.retrieval.qdrant import (
    LateInteractionEncoder,
    QdrantDocumentRepository,
    repo_root_for_path,
)
from codelens.workspace_runtime import parse_java_file_with_workspace

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexingResult:
    repo_root: str
    files_indexed: int
    documents_indexed: int


class QdrantIndexingService:
    def __init__(
        self,
        repository: QdrantDocumentRepository,
        encoder: LateInteractionEncoder,
    ) -> None:
        self._repository = repository
        self._encoder = encoder

    def index_file(
        self,
        filepath: str | Path,
        *,
        repo_root: str | Path,
        workspace_json: str | Path | None = None,
        resolve_binaries: bool = False,
        jdk_home: str | Path | None = None,
    ) -> IndexingResult:
        normalized_repo_root = repo_root_for_path(repo_root)
        normalized_file = str(Path(filepath).resolve())

        logger.info("Refreshing file %s in repo %s", normalized_file, normalized_repo_root)
        self._repository.delete_by_file(normalized_repo_root, normalized_file)

        if workspace_json is not None:
            chunks, context = parse_java_file_with_workspace(
                normalized_file,
                workspace_json=workspace_json,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
            )
            source_set = context.source_set_id.key if context.source_set_id else None
        else:
            code = Path(normalized_file).read_bytes()
            chunks = parse_java(code, filepath=normalized_file)
            source_set = None

        documents = build_retrieval_documents(
            chunks,
            repo_root=normalized_repo_root,
            source_set=source_set,
        )
        self._upsert_documents(documents)
        return IndexingResult(
            repo_root=normalized_repo_root,
            files_indexed=1,
            documents_indexed=len(documents),
        )

    def index_workspace(
        self,
        *,
        repo_root: str | Path,
        workspace_json: str | Path,
        resolve_binaries: bool = False,
        jdk_home: str | Path | None = None,
    ) -> IndexingResult:
        normalized_repo_root = repo_root_for_path(repo_root)
        workspace = GradleWorkspaceModel.from_json_file(workspace_json)
        filepaths = self._workspace_files(workspace)

        logger.info(
            "Rebuilding workspace index for repo %s with %d files",
            normalized_repo_root,
            len(filepaths),
        )
        self._repository.delete_by_repo(normalized_repo_root)

        total_documents = 0
        for filepath in filepaths:
            chunks, context = parse_java_file_with_workspace(
                filepath,
                workspace=workspace,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
            )
            source_set = context.source_set_id.key if context.source_set_id else None
            documents = build_retrieval_documents(
                chunks,
                repo_root=normalized_repo_root,
                source_set=source_set,
            )
            self._upsert_documents(documents)
            total_documents += len(documents)

        return IndexingResult(
            repo_root=normalized_repo_root,
            files_indexed=len(filepaths),
            documents_indexed=total_documents,
        )

    def _upsert_documents(self, documents: list[RetrievalDocument]) -> None:
        if not documents:
            return
        vectors = self._encoder.embed_documents([document.retrieval_text for document in documents])
        vector_size = len(vectors[0][0]) if vectors and vectors[0] else 0
        if vector_size <= 0:
            raise ValueError("Encoder returned empty multivectors")
        self._repository.ensure_collection(vector_size)
        self._repository.upsert_documents(documents, vectors=vectors)

    def _workspace_files(self, workspace: GradleWorkspaceModel) -> list[str]:
        files: set[str] = set()
        for source_set in workspace.source_sets.values():
            for root in source_set.all_roots:
                root_path = Path(root)
                if not root_path.exists():
                    continue
                for filepath in root_path.rglob("*.java"):
                    if filepath.is_file():
                        files.add(str(filepath.resolve()))
        return sorted(files)
