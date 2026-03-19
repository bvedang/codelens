from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import faiss
import numpy as np

from codelens.db.session import get_session
from codelens.indexing.models import (
    IndexStatus,
    LoadedIndex,
    MetadataCorruptionError,
)
from codelens.logging_config import get_logger
from codelens.models.index_chunk import IndexBuildState, IndexChunk, IndexMeta
from codelens.repository.index_chunk_repo import (
    delete_index_chunks_by_repo,
    delete_index_metadata,
    get_index_build_status,
    get_index_chunks,
    get_index_metadata,
    insert_index_chunks,
    upsert_index_build_status,
    upsert_index_metadata,
)

logger = get_logger(__name__)

class FaissIndexRepository:
    def __init__(self, repo_root: str | Path, *, faiss_module=None) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._index_dir = self._repo_root / ".codelens" / "index"
        self._vectors_path = self._index_dir / "vectors.faiss"
        self._shards_dir = self._index_dir / "shards"
        self._lock_path = self._index_dir / "index.lock"

    @property
    def index_dir(self) -> Path:
        return self._index_dir

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def load(self) -> LoadedIndex | None:
        with get_session() as session:
            index_metadata = get_index_metadata(session, str(self._repo_root))
            if not index_metadata:
                logger.warning("<faiss_repository / load>: unable to load index. Repo may not be indexed")
                return None
            chunks = get_index_chunks(session, str(self._repo_root))
            if not chunks:
                logger.info("<faiss_repository / load>: No chunks found. Repo may not be indexed")
                return None

        index = self._read_index(self._vectors_path) if self._vectors_path.exists() else None

        vector_size = int(getattr(index, "d", 0)) if index is not None else 0
        return LoadedIndex(
            model_name=index_metadata.model_name,
            indexed_at=index_metadata.indexed_at,
            vector_size=vector_size,
            index=index,
            chunks=chunks,
        )

    def status(self) -> IndexStatus | None:
        loaded = self.load()
        if loaded is None:
            return None
        return IndexStatus(
            chunk_count=len(loaded.chunks),
            indexed_at=loaded.indexed_at,
            model_name=loaded.model_name,
        )

    def load_workspace_state(self) -> IndexBuildState | None:
        with get_session() as session:
            return get_index_build_status(session,str(self._repo_root))

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
            upsert_index_metadata(session, IndexMeta(
                repo_root=str(self._repo_root),
                model_name=model_name,
                indexed_at=indexed_at,
            ))

        state = IndexBuildState(
            repo_root=str(self._repo_root),
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

        next_shard_id = state.next_shard_id
        documents_indexed = state.documents_indexed

        if entries:
            shard_name = f"{next_shard_id:08d}.faiss"
            chunks, flat_vectors, vector_size = self._build_index_chunks(entries, vectors, shard=shard_name)

            self._shards_dir.mkdir(parents=True, exist_ok=True)
            self._write_flat_index(flat_vectors, vector_size, self._shards_dir / shard_name)

            with get_session() as session:
                insert_index_chunks(session, chunks)

            next_shard_id += 1
            documents_indexed += len(entries)

        completed_files = list(state.completed_files)
        if workspace_file not in completed_files:
            completed_files.append(workspace_file)
        failed_files = dict(state.failed_files)
        failed_files.pop(workspace_file, None)

        updated_state = IndexBuildState(
            repo_root=str(self._repo_root),
            status="in_progress",
            workspace_signature=state.workspace_signature,
            model_name=state.model_name,
            indexed_at=state.indexed_at,
            total_files=state.total_files,
            completed_files=list(completed_files),
            failed_files=failed_files,
            documents_indexed=documents_indexed,
            next_shard_id=next_shard_id,
        )
        return self._write_state(updated_state)

    def mark_workspace_file_failed(
        self,
        *,
        workspace_file: str,
        error: str,
    ) -> IndexBuildState:
        state = self._require_workspace_state()
        failed_files = dict(state.failed_files)
        failed_files[workspace_file] = error
        updated_state = IndexBuildState(
            repo_root=str(self._repo_root),
            status="in_progress",
            workspace_signature=state.workspace_signature,
            model_name=state.model_name,
            indexed_at=state.indexed_at,
            total_files=state.total_files,
            completed_files=state.completed_files,
            failed_files=failed_files,
            documents_indexed=state.documents_indexed,
            next_shard_id=state.next_shard_id,
        )
        self._write_state(updated_state)
        return updated_state

    def complete_workspace_build(self) -> IndexBuildState:
        state = self._require_workspace_state()
        final_status = "completed" if not state.failed_files else "partial"
        updated_state = IndexBuildState(
            repo_root=str(self._repo_root),
            status=final_status,
            workspace_signature=state.workspace_signature,
            model_name=state.model_name,
            indexed_at=state.indexed_at,
            total_files=state.total_files,
            completed_files=list(state.completed_files),
            failed_files=state.failed_files,
            documents_indexed=state.documents_indexed,
            next_shard_id=state.next_shard_id,
        )
        return self._write_state(updated_state)

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
            delete_index_chunks_by_repo(session, str(self._repo_root))
            insert_index_chunks(session, chunks)
            upsert_index_metadata(session,
                IndexMeta(repo_root=str(self._repo_root), model_name=model_name, indexed_at=indexed_at)
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
            if exclude_file_path and chunk.payload.get("file_path") == exclude_file_path:
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
                raise MetadataCorruptionError("cannot reconstruct vectors without a FAISS index")
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

    def _write_index_file(
        self,
        vectors: Sequence[Sequence[Sequence[float]]],
        path: Path,
    ) -> None:
        flat_vectors: list[list[float]] = []
        vector_size = 0
        for matrix in vectors:
            for row in matrix:
                if not row:
                    continue
                current_size = len(row)
                if vector_size == 0:
                    vector_size = current_size
                elif vector_size != current_size:
                    raise ValueError("all FAISS vectors must have the same size")
                flat_vectors.append([float(value) for value in row])
        if not flat_vectors:
            raise ValueError("cannot write an empty shard")
        self._write_flat_index(flat_vectors, vector_size, path)

    def _write_flat_index(
        self,
        flat_vectors: Sequence[Sequence[float]],
        vector_size: int,
        path: Path,
    ) -> None:

        index = cast(Any, faiss.IndexFlatIP(vector_size))
        index.add(np.asarray(flat_vectors, dtype="float32"))
        faiss.write_index(index, str(path))
        path.touch(exist_ok=True)

    def _read_index(self, path: Path):
        return faiss.read_index(str(path))

    def _require_workspace_state(self) -> IndexBuildState:
        state = self.load_workspace_state()
        if state is None:
            raise MetadataCorruptionError("state.json is missing")
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
            delete_index_chunks_by_repo(session, str(self._repo_root))
            delete_index_metadata(session, str(self._repo_root))


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
            chunks.append(IndexChunk(
                chunk_id=chunk_id,
                repo_root=str(self._repo_root),
                faiss_ids=faiss_ids,
                payload=dict(entry),
                shard=shard,
            ))
        return chunks, flat_vectors, vector_size
