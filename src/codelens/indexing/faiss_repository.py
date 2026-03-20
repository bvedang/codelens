from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from hashlib import sha1
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np

from codelens.db.session import get_session
from codelens.indexing.models import (
    IndexStatus,
    LoadedIndex,
    MetadataCorruptionError,
)
from codelens.logging_config import get_logger, log_event
from codelens.models.index_chunk import IndexBuildState, IndexChunk, IndexMeta
from codelens.repository.index_chunk_repo import (
    delete_index_chunks_by_repo,
    delete_index_metadata,
    get_index_build_status,
    get_index_chunks,
    get_index_metadata,
    insert_index_chunks,
    replace_index_chunks_by_prefix,
    upsert_index_build_status,
    upsert_index_metadata,
)
from codelens.timing import Stopwatch

logger = get_logger(__name__)


class FaissIndexRepository:
    def __init__(self, repo_root: str | Path, *, faiss_module=None) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._repo_key = str(self._repo_root)
        self._repo_hash = sha1(self._repo_key.encode("utf-8")).hexdigest()[:12]
        self._index_dir = self._repo_root / ".codelens" / "index"
        self._vectors_path = self._index_dir / "vectors.faiss"
        self._shards_dir = self._index_dir / "shards"
        self._faiss_module = faiss_module

    @property
    def index_dir(self) -> Path:
        return self._index_dir

    def load(self) -> LoadedIndex | None:
        with get_session() as session:
            index_metadata = get_index_metadata(session, self._repo_key)
            if not index_metadata:
                logger.warning(
                    "<faiss_repository / load>: unable to load index. Repo may not be indexed"
                )
                return None
            chunks = get_index_chunks(session, self._repo_key)
            if not chunks:
                logger.info(
                    "<faiss_repository / load>: No chunks found. Repo may not be indexed"
                )
                return None

        index = (
            self._read_index(self._vectors_path)
            if self._vectors_path.exists()
            else None
        )

        vector_size = int(getattr(index, "d", 0)) if index is not None else 0
        return LoadedIndex(
            model_name=index_metadata.model_name,
            indexed_at=index_metadata.indexed_at,
            vector_size=vector_size,
            index=index,
            chunks=chunks,
        )

    def status(self) -> IndexStatus | None:
        with get_session() as session:
            metadata = get_index_metadata(session, self._repo_key)
            if not metadata:
                return None
            chunks = get_index_chunks(session, self._repo_key)
        return IndexStatus(
            chunk_count=len(chunks),
            indexed_at=metadata.indexed_at,
            model_name=metadata.model_name,
        )

    def load_workspace_state(self) -> IndexBuildState | None:
        with get_session() as session:
            return get_index_build_status(session, self._repo_key)

    def start_workspace_build(
        self,
        *,
        filepaths: Sequence[str],
        workspace_signature: str,
        model_name: str,
        indexed_at: str,
    ) -> IndexBuildState:
        existing = self.load_workspace_state()
        if (
            existing is not None
            and existing.status in {"in_progress", "partial"}
            and existing.workspace_signature == workspace_signature
            and existing.model_name == model_name
        ):
            return existing

        self._reset_index_dir()
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._shards_dir.mkdir(parents=True, exist_ok=True)

        with get_session() as session:
            upsert_index_metadata(
                session,
                IndexMeta(
                    repo_root=self._repo_key,
                    model_name=model_name,
                    indexed_at=indexed_at,
                ),
            )

        state = IndexBuildState(
            repo_root=self._repo_key,
            status="in_progress",
            workspace_signature=workspace_signature,
            model_name=model_name,
            indexed_at=indexed_at,
            total_files=len(filepaths),
            completed_files=[],
            failed_files={},
            documents_indexed=0,
            next_shard_id=0,
        )

        return self._write_state(state)

    def append_workspace_file(
        self,
        *,
        workspace_file: str,
        entries: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[Sequence[float]]],
    ) -> IndexBuildState:
        if len(entries) != len(vectors):
            raise ValueError("entries and vectors must have the same length")

        state = self._require_workspace_state()
        relative_file_path = self._relative_file_path(workspace_file)
        chunk_id_prefix = f"{self._repo_hash}:{relative_file_path}:"

        next_shard_id = state.next_shard_id
        documents_indexed = state.documents_indexed

        # Build chunks and write the shard file BEFORE touching the database.
        # This keeps the FAISS file consistent with what the DB will reference.
        new_chunks: list[IndexChunk] = []
        if entries:
            shard_name = f"{next_shard_id:08d}.faiss"
            new_chunks, flat_vectors, vector_size = self._build_index_chunks(
                entries, vectors, shard=shard_name
            )
            self._shards_dir.mkdir(parents=True, exist_ok=True)
            self._write_flat_index(
                flat_vectors, vector_size, self._shards_dir / shard_name
            )

        # Atomic delete-then-insert: old chunks are never missing without
        # their replacements being present in the same committed transaction.
        with get_session() as session:
            removed_count = replace_index_chunks_by_prefix(
                session,
                self._repo_key,
                chunk_id_prefix,
                new_chunks,
            )

        if removed_count:
            documents_indexed = max(0, documents_indexed - removed_count)
        if entries:
            next_shard_id += 1
            documents_indexed += len(entries)

        completed_files = list(state.completed_files)
        if workspace_file not in completed_files:
            completed_files.append(workspace_file)
        failed_files = dict(state.failed_files)
        failed_files.pop(workspace_file, None)

        updated = state.model_copy(
            update={
                "completed_files": completed_files,
                "failed_files": failed_files,
                "documents_indexed": documents_indexed,
                "next_shard_id": next_shard_id,
            }
        )
        return self._write_state(updated)

    def mark_workspace_file_failed(
        self,
        *,
        workspace_file: str,
        error: str,
    ) -> IndexBuildState:
        state = self._require_workspace_state()
        failed_files = dict(state.failed_files)
        failed_files[workspace_file] = error
        updated = state.model_copy(update={"failed_files": failed_files})
        return self._write_state(updated)

    def complete_workspace_build(self) -> IndexBuildState:
        state = self._require_workspace_state()
        final_status = "completed" if not state.failed_files else "partial"
        updated = state.model_copy(update={"status": final_status})
        return self._write_state(updated)

    def save(
        self,
        *,
        entries: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[Sequence[float]]],
        model_name: str,
        indexed_at: str,
    ) -> None:
        if len(entries) != len(vectors):
            raise ValueError("entries and vectors must have the same length")

        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._clear_shards()

        chunks, flat_vectors, vector_size = self._build_index_chunks(entries, vectors)
        if flat_vectors:
            self._write_flat_index(flat_vectors, vector_size, self._vectors_path)
        elif self._vectors_path.exists():
            self._vectors_path.unlink()
        with get_session() as session:
            delete_index_chunks_by_repo(session, self._repo_key)
            insert_index_chunks(session, chunks)
            upsert_index_metadata(
                session,
                IndexMeta(
                    repo_root=self._repo_key,
                    model_name=model_name,
                    indexed_at=indexed_at,
                ),
            )

    def entries_with_vectors(
        self,
        *,
        exclude_file_path: str | None = None,
    ) -> list[tuple[dict[str, Any], list[list[float]]]]:
        loaded = self.load()
        if loaded is None:
            return []

        retained: list[tuple[dict[str, Any], list[list[float]]]] = []
        shard_cache: dict[str, Any] = {}
        for chunk in loaded.chunks.values():
            if (
                exclude_file_path
                and chunk.payload.get("file_path") == exclude_file_path
            ):
                continue
            retained.append(
                (
                    dict(chunk.payload),
                    self._reconstruct_vectors(
                        loaded=loaded,
                        faiss_ids=chunk.faiss_ids,
                        shard=chunk.shard,
                        shard_cache=shard_cache,
                    ),
                )
            )
        return retained

    def search(
        self,
        query_vectors: Sequence[Sequence[float]],
        *,
        top_k: int = 5,
        kind: str | None = None,
        source_set: str | None = None,
        file_path: str | None = None,
    ) -> list[tuple[dict[str, Any], float]]:
        if top_k <= 0:
            return []

        loaded = self.load()
        if loaded is None or not query_vectors:
            return []

        candidate_ids = self._candidate_chunk_ids(
            loaded=loaded,
            query_vectors=query_vectors,
            top_k=top_k,
            kind=kind,
            source_set=source_set,
            file_path=file_path,
        )
        if not candidate_ids:
            return []

        matches: list[tuple[dict[str, Any], float]] = []
        shard_cache: dict[str, Any] = {}

        for chunk_id in candidate_ids:
            chunk = loaded.chunks[chunk_id]
            payload = dict(chunk.payload)
            document_vectors = self._reconstruct_vectors(
                loaded=loaded,
                faiss_ids=chunk.faiss_ids,
                shard=chunk.shard,
                shard_cache=shard_cache,
            )
            if not document_vectors:
                continue
            score = _late_interaction_score(query_vectors, document_vectors)
            matches.append((payload, score))

        matches.sort(key=lambda item: (-item[1], str(item[0].get("chunk_id", ""))))
        return matches[:top_k]

    def _candidate_chunk_ids(
        self,
        *,
        loaded: LoadedIndex,
        query_vectors: Sequence[Sequence[float]],
        top_k: int,
        kind: str | None,
        source_set: str | None,
        file_path: str | None,
    ) -> list[str]:
        candidate_limit = max(top_k * 20, 100)
        per_query_limit = max(top_k * 8, 32)

        filtered_chunks = [
            chunk
            for chunk in loaded.chunks.values()
            if chunk.faiss_ids
            and _matches_search_filters(
                chunk.payload,
                kind=kind,
                source_set=source_set,
                file_path=file_path,
            )
        ]
        if not filtered_chunks:
            return []

        if len(filtered_chunks) <= candidate_limit:
            return [
                chunk.chunk_id
                for chunk in sorted(filtered_chunks, key=lambda item: item.chunk_id)
            ]

        faiss = self._faiss()
        if hasattr(faiss, "omp_set_num_threads"):
            faiss.omp_set_num_threads(1)
            log_event(
                logger,
                level=logging.INFO,
                message="Configured FAISS search threads",
                threads=1,
            )

        query_matrix = _prepare_query_matrix(query_vectors)
        shard_groups = _group_chunks_by_shard(filtered_chunks)
        shard_cache: dict[str, Any] = {}
        candidate_scores: dict[str, float] = defaultdict(float)
        total_shards = len(shard_groups)
        progress_timer = Stopwatch.start()

        log_event(
            logger,
            level=logging.INFO,
            message="Starting FAISS candidate retrieval",
            shards=total_shards,
            filtered_chunks=len(filtered_chunks),
            candidate_limit=candidate_limit,
            per_query_limit=per_query_limit,
            query_rows=int(query_matrix.shape[0]),
            query_dim=int(query_matrix.shape[1]) if query_matrix.ndim == 2 else 0,
            query_min=float(np.min(query_matrix)) if query_matrix.size else 0.0,
            query_max=float(np.max(query_matrix)) if query_matrix.size else 0.0,
        )

        for shard_index, (shard, chunks) in enumerate(shard_groups.items(), start=1):
            should_log_shard = (
                shard_index == 1 or shard_index == total_shards or shard_index % 50 == 0
            )
            if should_log_shard:
                log_event(
                    logger,
                    level=logging.INFO,
                    message="Opening FAISS shard",
                    shard=shard or "vectors.faiss",
                    shard_index=shard_index,
                    total_shards=total_shards,
                    shard_chunks=len(chunks),
                    elapsed_ms=progress_timer.elapsed_ms,
                )
            index = self._search_index(
                loaded=loaded,
                shard=shard,
                shard_cache=shard_cache,
            )
            if should_log_shard:
                log_event(
                    logger,
                    level=logging.INFO,
                    message="Opened FAISS shard",
                    shard=shard or "vectors.faiss",
                    shard_index=shard_index,
                    total_shards=total_shards,
                    elapsed_ms=progress_timer.elapsed_ms,
                )
            faiss_id_to_chunk = _faiss_id_to_chunk_map(chunks)
            if not faiss_id_to_chunk:
                continue

            limit = min(per_query_limit, len(faiss_id_to_chunk))
            if should_log_shard:
                log_event(
                    logger,
                    level=logging.INFO,
                    message="Searching FAISS shard",
                    shard=shard or "vectors.faiss",
                    shard_index=shard_index,
                    total_shards=total_shards,
                    shard_vectors=len(faiss_id_to_chunk),
                    search_limit=limit,
                    elapsed_ms=progress_timer.elapsed_ms,
                )
            raw_scores, raw_ids = index.search(query_matrix, limit)
            if should_log_shard:
                log_event(
                    logger,
                    level=logging.INFO,
                    message="Searched FAISS shard",
                    shard=shard or "vectors.faiss",
                    shard_index=shard_index,
                    total_shards=total_shards,
                    elapsed_ms=progress_timer.elapsed_ms,
                )
            for score_row, id_row in zip(raw_scores, raw_ids, strict=True):
                row_best: dict[str, float] = {}
                for score, faiss_id in zip(score_row, id_row, strict=True):
                    faiss_id_int = int(faiss_id)
                    if faiss_id_int < 0:
                        continue
                    chunk_id = faiss_id_to_chunk.get(faiss_id_int)
                    if chunk_id is None:
                        continue
                    score_value = float(score)
                    previous = row_best.get(chunk_id)
                    if previous is None or score_value > previous:
                        row_best[chunk_id] = score_value
                for chunk_id, score in row_best.items():
                    candidate_scores[chunk_id] += score

            if should_log_shard:
                log_event(
                    logger,
                    level=logging.INFO,
                    message="FAISS candidate retrieval progress",
                    shards_processed=shard_index,
                    total_shards=total_shards,
                    candidate_chunks=len(candidate_scores),
                    elapsed_ms=progress_timer.elapsed_ms,
                )

        ranked = sorted(candidate_scores.items(), key=lambda item: (-item[1], item[0]))
        return [chunk_id for chunk_id, _ in ranked[:candidate_limit]]

    def _reconstruct_vectors(
        self,
        *,
        loaded: LoadedIndex,
        faiss_ids: Sequence[int],
        shard: str | None,
        shard_cache: dict[str, Any],
    ) -> list[list[float]]:
        if not faiss_ids:
            return []
        if shard is None:
            if loaded.index is None:
                raise MetadataCorruptionError(
                    "cannot reconstruct vectors without a FAISS index"
                )
            index = cast(Any, loaded.index)
        else:
            if shard not in shard_cache:
                shard_cache[shard] = self._read_index(self._shards_dir / shard)
            index = cast(Any, shard_cache[shard])
        rows: list[list[float]] = []
        for faiss_id in faiss_ids:
            vector = index.reconstruct(int(faiss_id))
            rows.append([float(value) for value in vector])
        return rows

    def _search_index(
        self,
        *,
        loaded: LoadedIndex,
        shard: str | None,
        shard_cache: dict[str, Any],
    ):
        if shard is None:
            if loaded.index is None:
                raise MetadataCorruptionError(
                    "cannot search vectors without a FAISS index"
                )
            return cast(Any, loaded.index)
        if shard not in shard_cache:
            shard_cache[shard] = self._read_index(self._shards_dir / shard)
        return cast(Any, shard_cache[shard])

    def _write_flat_index(
        self,
        flat_vectors: Sequence[Sequence[float]],
        vector_size: int,
        path: Path,
    ) -> None:
        faiss = self._faiss()
        index = cast(Any, faiss.IndexFlatIP(vector_size))
        index.add(np.asarray(flat_vectors, dtype="float32"))
        faiss.write_index(index, str(path))
        path.touch(exist_ok=True)

    def _read_index(self, path: Path):
        return self._faiss().read_index(str(path))

    def _require_workspace_state(self) -> IndexBuildState:
        state = self.load_workspace_state()
        if state is None:
            raise MetadataCorruptionError("workspace build state is missing")
        return state

    def _write_state(self, state: IndexBuildState) -> IndexBuildState:
        with get_session() as session:
            return upsert_index_build_status(session, state)

    def _reset_index_dir(self) -> None:
        self._index_dir.mkdir(parents=True, exist_ok=True)
        if self._vectors_path.exists():
            self._vectors_path.unlink()
        self._clear_shards()
        with get_session() as session:
            delete_index_chunks_by_repo(session, self._repo_key)
            delete_index_metadata(session, self._repo_key)

    def _clear_shards(self) -> None:
        if self._shards_dir.exists():
            shutil.rmtree(self._shards_dir)

    def _build_index_chunks(
        self,
        entries: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[Sequence[float]]],
        *,
        shard: str | None = None,
    ) -> tuple[list[IndexChunk], list[list[float]], int]:
        flat_vectors: list[list[float]] = []
        chunks: list[IndexChunk] = []
        next_id = 0
        vector_size = 0

        for entry, matrix in zip(entries, vectors, strict=True):
            chunk_id = str(entry["chunk_id"])
            faiss_ids: list[int] = []
            for row in matrix:
                if not row:
                    continue
                current_size = len(row)
                if vector_size == 0:
                    vector_size = current_size
                elif vector_size != current_size:
                    raise ValueError("all FAISS vectors must have the same size")
                flat_vectors.append([float(value) for value in row])
                faiss_ids.append(next_id)
                next_id += 1
            chunks.append(
                IndexChunk(
                    chunk_id=chunk_id,
                    repo_root=self._repo_key,
                    faiss_ids=faiss_ids,
                    payload=dict(entry),
                    shard=shard,
                )
            )
        return chunks, flat_vectors, vector_size

    def _faiss(self):
        if self._faiss_module is None:
            import faiss

            self._faiss_module = faiss
        return self._faiss_module

    def _relative_file_path(self, workspace_file: str | Path) -> str:
        path = Path(workspace_file).resolve()
        try:
            return path.relative_to(self._repo_root).as_posix()
        except ValueError as exc:
            raise MetadataCorruptionError(
                f"workspace file is outside repo root: {path}"
            ) from exc


def _matches_search_filters(
    payload: Mapping[str, Any],
    *,
    kind: str | None,
    source_set: str | None,
    file_path: str | None,
) -> bool:
    if kind and payload.get("chunk_kind") != kind:
        return False
    if source_set and payload.get("source_set") != source_set:
        return False
    if file_path and payload.get("file_path") != file_path:
        return False
    return True


def _late_interaction_score(
    query_vectors: Sequence[Sequence[float]],
    document_vectors: Sequence[Sequence[float]],
) -> float:
    total = 0.0
    for query_vector in query_vectors:
        best = float("-inf")
        for document_vector in document_vectors:
            score = _dot_product(query_vector, document_vector)
            if score > best:
                best = score
        if best != float("-inf"):
            total += best
    return total


def _dot_product(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("query and document vectors must have the same length")
    return float(sum(float(a) * float(b) for a, b in zip(left, right, strict=True)))


def _prepare_query_matrix(query_vectors: Sequence[Sequence[float]]) -> np.ndarray:
    matrix = np.asarray(query_vectors, dtype="float32")
    if matrix.ndim != 2:
        raise ValueError("query vectors must be a 2D matrix")
    if matrix.size == 0:
        return np.ascontiguousarray(matrix)
    matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    return np.ascontiguousarray(matrix)


def _group_chunks_by_shard(
    chunks: Sequence[IndexChunk] | Sequence[Any],
) -> dict[str | None, list[Any]]:
    grouped: dict[str | None, list[Any]] = defaultdict(list)
    for chunk in chunks:
        grouped[chunk.shard].append(chunk)
    return grouped


def _faiss_id_to_chunk_map(chunks: Sequence[Any]) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for chunk in chunks:
        for faiss_id in chunk.faiss_ids:
            mapping[int(faiss_id)] = str(chunk.chunk_id)
    return mapping
