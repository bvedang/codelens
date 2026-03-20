from __future__ import annotations

import gc
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path

from codelens.gradle_model import GradleWorkspaceModel
from codelens.indexing.documents import build_index_documents, document_payload
from codelens.indexing.encoder import LateInteractionEncoder
from codelens.indexing.faiss_repository import FaissIndexRepository, MetadataCorruptionError
from codelens.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexingResult:
    repo_root: str
    files_indexed: int
    documents_indexed: int
    failures: int = 0


class FaissIndexingService:
    def __init__(
        self,
        repository: FaissIndexRepository,
        encoder: LateInteractionEncoder,
        *,
        batch_size: int = 32,
    ) -> None:
        self._repository = repository
        self._encoder = encoder
        self._batch_size = batch_size

    def index_workspace(
        self,
        *,
        repo_root: str | Path,
        workspace_json: str | Path,
        resolve_binaries: bool = False,
        jdk_home: str | Path | None = None,
    ) -> IndexingResult:
        normalized_repo_root = str(Path(repo_root).resolve())
        workspace = GradleWorkspaceModel.from_json_file(workspace_json)
        filepaths = self._workspace_files(workspace)
        workspace_signature = _workspace_signature(filepaths)
        indexed_at = _indexed_at()

        log_event(
            logger,
            level=logging.INFO,
            message="Preparing workspace index",
            repo_root=normalized_repo_root,
            files=len(filepaths),
            batch_size=self._batch_size,
            model=self._encoder.model_name,
        )
        self._prepare_encoder()

        state = self._repository.start_workspace_build(
            filepaths=filepaths,
            workspace_signature=workspace_signature,
            model_name=self._encoder.model_name,
            indexed_at=indexed_at,
        )
        return self._index_workspace_locked(
            repo_root=normalized_repo_root,
            workspace=workspace,
            filepaths=filepaths,
            state=state,
            resolve_binaries=resolve_binaries,
            jdk_home=jdk_home,
        )

    def index_file(
        self,
        filepath: str | Path,
        *,
        repo_root: str | Path,
        workspace_json: str | Path | None = None,
        resolve_binaries: bool = False,
        jdk_home: str | Path | None = None,
    ) -> IndexingResult:
        file_path = Path(filepath).resolve()
        if not file_path.exists():
            raise ValueError(f"File does not exist: {file_path}")

        normalized_repo_root = str(Path(repo_root).resolve())
        indexed_at = _indexed_at()
        relative_file_path = file_path.relative_to(Path(normalized_repo_root)).as_posix()

        log_event(
            logger,
            level=logging.INFO,
            message="Refreshing indexed file",
            repo_root=normalized_repo_root,
            file=relative_file_path,
            batch_size=self._batch_size,
            model=self._encoder.model_name,
        )
        self._prepare_encoder()

        workspace = (
            GradleWorkspaceModel.from_json_file(workspace_json)
            if workspace_json is not None
            else None
        )

        try:
            retained = self._repository.entries_with_vectors(
                exclude_file_path=relative_file_path,
            )
        except MetadataCorruptionError:
            if workspace is None:
                raise
            logger.error("Stored metadata is corrupted; forcing full workspace rebuild")
            return self._index_workspace_locked(
                repo_root=normalized_repo_root,
                workspace=workspace,
                indexed_at=indexed_at,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
            )

        documents = self._parse_index_documents(
            file_path,
            repo_root=normalized_repo_root,
            indexed_at=indexed_at,
            workspace=workspace,
            resolve_binaries=resolve_binaries,
            jdk_home=jdk_home,
        )
        new_vectors = self._embed_documents(documents)
        entries = [payload for payload, _ in retained]
        vectors = [matrix for _, matrix in retained]
        entries.extend(document_payload(document) for document in documents)
        vectors.extend(new_vectors)
        self._repository.save(
            entries=entries,
            vectors=vectors,
            model_name=self._encoder.model_name,
            indexed_at=indexed_at,
        )

        return IndexingResult(
            repo_root=normalized_repo_root,
            files_indexed=1,
            documents_indexed=len(documents),
        )

    def _index_workspace_locked(
        self,
        *,
        repo_root: str,
        workspace: GradleWorkspaceModel,
        filepaths: list[str],
        state,
        resolve_binaries: bool,
        jdk_home: str | Path | None,
    ) -> IndexingResult:
        completed_files = set(state.completed_files)
        total_files = len(filepaths)

        log_event(
            logger,
            level=logging.INFO,
            message="Workspace indexing started",
            repo_root=repo_root,
            total_files=total_files,
            already_completed=len(completed_files),
        )

        for index, filepath in enumerate(filepaths, start=1):
            if filepath in completed_files:
                continue
            try:
                documents = self._parse_index_documents(
                    Path(filepath),
                    repo_root=repo_root,
                    indexed_at=state.indexed_at,
                    workspace=workspace,
                    resolve_binaries=resolve_binaries,
                    jdk_home=jdk_home,
                )
                vectors = self._embed_documents(documents)
                state = self._repository.append_workspace_file(
                    workspace_file=filepath,
                    entries=[document_payload(document) for document in documents],
                    vectors=vectors,
                )
                self._log_workspace_progress(
                    repo_root=repo_root,
                    filepath=filepath,
                    current=index,
                    total=total_files,
                    documents_in_file=len(documents),
                    documents_indexed=state.documents_indexed,
                )
            except Exception as exc:
                state = self._repository.mark_workspace_file_failed(
                    workspace_file=filepath,
                    error=str(exc),
                )
                logger.exception("Failed to parse %s during workspace indexing", filepath)
                continue

        state = self._repository.complete_workspace_build()
        log_event(
            logger,
            level=logging.INFO,
            message="Workspace indexing finished",
            repo_root=repo_root,
            completed_files=len(state.completed_files),
            failed_files=len(state.failed_files),
            documents_indexed=state.documents_indexed,
        )
        return IndexingResult(
            repo_root=repo_root,
            files_indexed=len(state.completed_files),
            documents_indexed=state.documents_indexed,
            failures=len(state.failed_files),
        )

    def _parse_index_documents(
        self,
        filepath: Path,
        *,
        repo_root: str,
        indexed_at: str,
        workspace: GradleWorkspaceModel | None,
        resolve_binaries: bool,
        jdk_home: str | Path | None,
    ):
        if workspace is not None:
            from codelens.workspace_runtime import parse_java_file_with_workspace

            chunks, context = parse_java_file_with_workspace(
                filepath,
                workspace=workspace,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
            )
            source_set = context.source_set_id.key if context.source_set_id else None
        else:
            from codelens.chunker import parse_java

            chunks = parse_java(filepath.read_bytes(), filepath=str(filepath))
            source_set = None
        return build_index_documents(
            chunks,
            repo_root=repo_root,
            indexed_at=indexed_at,
            source_set=source_set,
        )

    def _embed_documents(self, documents) -> list[list[list[float]]]:
        vectors: list[list[list[float]]] = []
        for start in range(0, len(documents), self._batch_size):
            batch = documents[start : start + self._batch_size]
            if not batch:
                continue
            batch_vectors = self._embed_batch(batch)
            vectors.extend(batch_vectors)
        return vectors

    def _embed_batch(self, documents) -> list[list[list[float]]]:
        try:
            return self._encoder.embed_documents(
                [document.retrieval_text for document in documents]
            )
        except Exception as exc:
            if len(documents) == 1 or not _is_out_of_memory_error(exc):
                raise

            logger.warning(
                "Embedding batch of %s documents hit OOM; retrying with smaller batches",
                len(documents),
            )
            _release_accelerator_memory()
            midpoint = len(documents) // 2
            return self._embed_batch(documents[:midpoint]) + self._embed_batch(documents[midpoint:])

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

    def _prepare_encoder(self) -> None:
        log_event(
            logger,
            level=logging.INFO,
            message="Preloading encoder model",
            model=self._encoder.model_name,
        )
        self._encoder.prepare()

    def _log_workspace_progress(
        self,
        *,
        repo_root: str,
        filepath: str,
        current: int,
        total: int,
        documents_in_file: int,
        documents_indexed: int,
    ) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            level = logging.DEBUG
        elif current == total or current == 1 or current % 25 == 0:
            level = logging.INFO
        else:
            return

        relative_path = Path(filepath).relative_to(Path(repo_root)).as_posix()
        log_event(
            logger,
            level=level,
            message="Indexed workspace file",
            progress=f"{current}/{total}",
            file=relative_path,
            documents_in_file=documents_in_file,
            documents_indexed=documents_indexed,
        )


def _indexed_at() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _workspace_signature(filepaths: list[str]) -> str:
    digest = sha1()
    for filepath in filepaths:
        digest.update(filepath.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _is_out_of_memory_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "oom" in message


def _release_accelerator_memory() -> None:
    gc.collect()
    try:
        import torch
    except ImportError:
        return

    if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        torch.mps.empty_cache()
    if hasattr(torch, "cuda") and hasattr(torch.cuda, "empty_cache"):
        torch.cuda.empty_cache()
