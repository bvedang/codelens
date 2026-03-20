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
from codelens.timing import TimingCollector, Stopwatch, measure

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexingResult:
    repo_root: str
    files_indexed: int
    documents_indexed: int
    failures: int = 0


@dataclass(frozen=True)
class WorkspaceIndexSelection:
    workspace: GradleWorkspaceModel
    filepaths: list[str]
    deferred_source_set_names: tuple[str, ...]
    deferred_files: int


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
        full_workspace = GradleWorkspaceModel.from_json_file(workspace_json)
        selection = self._select_workspace_index_files(full_workspace)
        workspace = selection.workspace
        filepaths = selection.filepaths
        workspace_signature = _workspace_signature(filepaths)
        indexed_at = _indexed_at()

        log_event(
            logger,
            level=logging.INFO,
            message="Preparing workspace index",
            repo_root=normalized_repo_root,
            files=len(filepaths),
            scope="main-only",
            deferred_source_sets=",".join(selection.deferred_source_set_names) or "none",
            deferred_files=selection.deferred_files,
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
        workspace_context = None
        if workspace is not None:
            file_source_sets, source_set_file_counts = self._workspace_source_sets(
                [str(file_path)],
                workspace,
            )
            workspace_context = self._build_workspace_context(
                file_source_sets[str(file_path)],
                workspace=workspace,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
                files_in_source_set=source_set_file_counts[file_source_sets[str(file_path)]],
            )

        try:
            retained = self._repository.entries_with_vectors(
                exclude_file_path=relative_file_path,
            )
        except MetadataCorruptionError:
            if workspace is None:
                raise
            logger.error("Stored metadata is corrupted; forcing full workspace rebuild")
            selection = self._select_workspace_index_files(workspace)
            workspace = selection.workspace
            filepaths = selection.filepaths
            state = self._repository.start_workspace_build(
                filepaths=filepaths,
                workspace_signature=_workspace_signature(filepaths),
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

        with measure() as parse_timer:
            documents = self._parse_index_documents(
                file_path,
                repo_root=normalized_repo_root,
                indexed_at=indexed_at,
                workspace=workspace,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
                workspace_context=workspace_context,
            )
        with measure() as embed_timer:
            new_vectors = self._embed_documents(documents)
        entries = [payload for payload, _ in retained]
        vectors = [matrix for _, matrix in retained]
        entries.extend(document_payload(document) for document in documents)
        vectors.extend(new_vectors)
        with measure() as persist_timer:
            self._repository.save(
                entries=entries,
                vectors=vectors,
                model_name=self._encoder.model_name,
                indexed_at=indexed_at,
            )
        log_event(
            logger,
            level=logging.INFO,
            message="Indexed file timings",
            file=relative_file_path,
            documents=len(documents),
            parse_ms=parse_timer.elapsed_ms,
            embed_ms=embed_timer.elapsed_ms,
            persist_ms=persist_timer.elapsed_ms,
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
        pending_filepaths = [filepath for filepath in filepaths if filepath not in completed_files]
        stage_timings = TimingCollector()
        total_timer = Stopwatch.start()

        log_event(
            logger,
            level=logging.INFO,
            message="Workspace indexing started",
            repo_root=repo_root,
            scope="main-only",
            total_files=total_files,
            already_completed=len(completed_files),
        )

        workspace_shared_context = self._build_workspace_shared_context(
            workspace=workspace,
            jdk_home=jdk_home,
        )
        file_source_sets, source_set_file_counts = self._workspace_source_sets(
            pending_filepaths,
            workspace,
        )
        workspace_contexts: dict[str | None, object] = {}

        for index, filepath in enumerate(filepaths, start=1):
            if filepath in completed_files:
                continue
            try:
                source_set_key = file_source_sets[filepath]
                workspace_context = workspace_contexts.get(source_set_key)
                if workspace_context is None:
                    workspace_context = self._build_workspace_context(
                        source_set_key,
                        workspace=workspace,
                        resolve_binaries=resolve_binaries,
                        jdk_home=jdk_home,
                        files_in_source_set=source_set_file_counts[source_set_key],
                        shared_context=workspace_shared_context,
                    )
                    workspace_contexts[source_set_key] = workspace_context

                with measure() as parse_timer:
                    documents = self._parse_index_documents(
                        Path(filepath),
                        repo_root=repo_root,
                        indexed_at=state.indexed_at,
                        workspace=workspace,
                        resolve_binaries=resolve_binaries,
                        jdk_home=jdk_home,
                        workspace_context=workspace_context,
                    )
                stage_timings.add("parse", parse_timer.elapsed_seconds)

                with measure() as embed_timer:
                    vectors = self._embed_documents(documents)
                stage_timings.add("embed", embed_timer.elapsed_seconds)

                with measure() as persist_timer:
                    state = self._repository.append_workspace_file(
                        workspace_file=filepath,
                        entries=[document_payload(document) for document in documents],
                        vectors=vectors,
                    )
                stage_timings.add("persist", persist_timer.elapsed_seconds)
                self._log_workspace_progress(
                    repo_root=repo_root,
                    filepath=filepath,
                    current=index,
                    total=total_files,
                    documents_in_file=len(documents),
                    documents_indexed=state.documents_indexed,
                    parse_ms=parse_timer.elapsed_ms,
                    embed_ms=embed_timer.elapsed_ms,
                    persist_ms=persist_timer.elapsed_ms,
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
            parse_ms=stage_timings.ms("parse"),
            embed_ms=stage_timings.ms("embed"),
            persist_ms=stage_timings.ms("persist"),
            total_ms=total_timer.elapsed_ms,
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
        workspace_context=None,
    ):
        if workspace is not None:
            if workspace_context is None:
                from codelens.workspace_runtime import build_workspace_resolver_context
                from codelens.workspace_runtime import parse_java_file_with_resolver_context

                context = build_workspace_resolver_context(
                    filepath,
                    workspace=workspace,
                    resolve_binaries=resolve_binaries,
                    jdk_home=jdk_home,
                )
                chunks, context = parse_java_file_with_resolver_context(
                    filepath,
                    context=context,
                )
            else:
                from codelens.workspace_runtime import parse_java_file_with_resolver_context

                chunks, context = parse_java_file_with_resolver_context(
                    filepath,
                    context=workspace_context,
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

    def _select_workspace_index_files(
        self,
        workspace: GradleWorkspaceModel,
    ) -> WorkspaceIndexSelection:
        selected_source_sets = {
            key: source_set
            for key, source_set in workspace.source_sets.items()
            if source_set.source_set_id.name == "main"
        }
        selected_workspace = GradleWorkspaceModel(
            source_sets=selected_source_sets,
            jdk_home=workspace.jdk_home,
        )
        selected_files = self._workspace_files(selected_workspace)

        deferred_source_set_names = sorted({
            source_set.source_set_id.name
            for source_set in workspace.source_sets.values()
            if source_set.source_set_id.name != "main"
        })
        deferred_files = 0
        if deferred_source_set_names:
            deferred_source_sets = {
                key: source_set
                for key, source_set in workspace.source_sets.items()
                if source_set.source_set_id.name != "main"
            }
            deferred_workspace = GradleWorkspaceModel(
                source_sets=deferred_source_sets,
                jdk_home=workspace.jdk_home,
            )
            deferred_files = len(self._workspace_files(deferred_workspace))

        return WorkspaceIndexSelection(
            workspace=selected_workspace,
            filepaths=selected_files,
            deferred_source_set_names=tuple(deferred_source_set_names),
            deferred_files=deferred_files,
        )

    def _prepare_encoder(self) -> None:
        log_event(
            logger,
            level=logging.INFO,
            message="Preloading encoder model",
            model=self._encoder.model_name,
        )
        self._encoder.prepare()

    def _workspace_source_sets(
        self,
        filepaths: list[str],
        workspace: GradleWorkspaceModel,
    ) -> tuple[dict[str, str | None], dict[str | None, int]]:
        file_source_sets: dict[str, str | None] = {}
        source_set_file_counts: dict[str | None, int] = {}

        for filepath in filepaths:
            source_set_id = workspace.source_set_for_file(filepath)
            source_set_key = source_set_id.key if source_set_id is not None else None
            file_source_sets[filepath] = source_set_key
            source_set_file_counts[source_set_key] = source_set_file_counts.get(source_set_key, 0) + 1
        return file_source_sets, source_set_file_counts

    def _build_workspace_context(
        self,
        source_set_key: str | None,
        *,
        workspace: GradleWorkspaceModel,
        resolve_binaries: bool,
        jdk_home: str | Path | None,
        files_in_source_set: int,
        shared_context=None,
    ):
        from codelens.workspace_runtime import build_workspace_source_set_context

        with measure() as context_timer:
            source_set_id = workspace.source_sets.get(source_set_key).source_set_id if source_set_key else None
            context = build_workspace_source_set_context(
                source_set_id,
                workspace=workspace,
                resolve_binaries=resolve_binaries,
                jdk_home=jdk_home,
                shared_context=shared_context,
            )
        log_event(
            logger,
            level=logging.INFO,
            message="Prepared workspace resolver context",
            source_set=source_set_key or "unmapped",
            files=files_in_source_set,
            duration_ms=context_timer.elapsed_ms,
        )
        return context

    def _build_workspace_shared_context(
        self,
        *,
        workspace: GradleWorkspaceModel,
        jdk_home: str | Path | None,
    ):
        from codelens.workspace_runtime import build_workspace_shared_context

        with measure() as context_timer:
            context = build_workspace_shared_context(
                workspace=workspace,
                jdk_home=jdk_home,
            )
        log_event(
            logger,
            level=logging.INFO,
            message="Prepared workspace shared context",
            source_roots=len({
                str(Path(root).resolve())
                for source_set in workspace.source_sets.values()
                for root in source_set.all_roots
                if Path(root).exists()
            }),
            duration_ms=context_timer.elapsed_ms,
        )
        return context

    def _log_workspace_progress(
        self,
        *,
        repo_root: str,
        filepath: str,
        current: int,
        total: int,
        documents_in_file: int,
        documents_indexed: int,
        parse_ms: int,
        embed_ms: int,
        persist_ms: int,
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
            parse_ms=parse_ms,
            embed_ms=embed_ms,
            persist_ms=persist_ms,
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
    memory_markers = (
        "out of memory",
        "oom",
        "invalid buffer size",
        "not enough memory",
        "insufficient memory",
        "cuda out of memory",
        "mps backend out of memory",
    )
    return any(marker in message for marker in memory_markers)


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
