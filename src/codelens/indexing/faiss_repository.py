from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import faiss
import numpy as np

from codelens.indexing.models import (
    IndexStatus,
    LoadedIndex,
    MetadataCorruptionError,
    StoredChunk,
    WorkspaceBuildState,
)


class FaissIndexRepository:
    def __init__(self, repo_root: str | Path, *, faiss_module=None) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._index_dir = self._repo_root / ".codelens" / "index"
        self._vectors_path = self._index_dir / "vectors.faiss"
        self._metadata_path = self._index_dir / "metadata.json"
        self._state_path = self._index_dir / "state.json"
        self._shards_dir = self._index_dir / "shards"
        self._lock_path = self._index_dir / "index.lock"

    @property
    def index_dir(self) -> Path:
        return self._index_dir

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def load(self) -> LoadedIndex | None:
        metadata = self._load_metadata()
        if metadata is None:
            return None

        raw_chunks = metadata.get("chunks")
        if not isinstance(raw_chunks, dict):
            raise MetadataCorruptionError("metadata.json is missing the 'chunks' object")

        index = self._read_index(self._vectors_path) if self._vectors_path.exists() else None
        chunks: dict[str, StoredChunk] = {}
        for chunk_id, item in raw_chunks.items():
            if not isinstance(item, dict):
                raise MetadataCorruptionError(f"chunk entry {chunk_id!r} is not an object")
            faiss_ids = item.get("faiss_ids")
            payload = item.get("payload")
            shard = item.get("shard")
            if not isinstance(faiss_ids, list) or not isinstance(payload, dict):
                raise MetadataCorruptionError(f"chunk entry {chunk_id!r} is malformed")
            if shard is not None and not isinstance(shard, str):
                raise MetadataCorruptionError(f"chunk entry {chunk_id!r} has an invalid shard")
            if shard is None and index is None and raw_chunks:
                raise MetadataCorruptionError("vectors.faiss is missing")
            if shard is not None and not (self._shards_dir / shard).exists():
                raise MetadataCorruptionError(f"shard file is missing for chunk {chunk_id!r}")
            chunks[str(chunk_id)] = StoredChunk(
                chunk_id=str(chunk_id),
                faiss_ids=tuple(int(value) for value in faiss_ids),
                payload=dict(payload),
                shard=shard,
            )

        vector_size = int(getattr(index, "d", 0)) if index is not None else 0
        return LoadedIndex(
            model_name=metadata.get("model"),
            indexed_at=metadata.get("indexed_at"),
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

    def load_workspace_state(self) -> WorkspaceBuildState | None:
        if not self._state_path.exists():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MetadataCorruptionError("state.json is not valid JSON") from exc
        return WorkspaceBuildState(
            status=str(raw["status"]),
            workspace_signature=str(raw["workspace_signature"]),
            model_name=str(raw["model_name"]),
            indexed_at=str(raw["indexed_at"]),
            total_files=int(raw["total_files"]),
            completed_files=tuple(str(item) for item in raw.get("completed_files", [])),
            failed_files={str(key): str(value) for key, value in raw.get("failed_files", {}).items()},
            documents_indexed=int(raw.get("documents_indexed", 0)),
            next_shard_id=int(raw.get("next_shard_id", 0)),
        )

    def start_workspace_build(
        self,
        *,
        filepaths: Sequence[str],
        workspace_signature: str,
        model_name: str,
        indexed_at: str,
    ) -> WorkspaceBuildState:
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

        metadata = {
            "model": model_name,
            "indexed_at": indexed_at,
            "chunks": {},
        }
        state = WorkspaceBuildState(
            status="in_progress",
            workspace_signature=workspace_signature,
            model_name=model_name,
            indexed_at=indexed_at,
            total_files=len(filepaths),
            completed_files=(),
            failed_files={},
            documents_indexed=0,
            next_shard_id=0,
        )
        self._write_metadata(metadata)
        self._write_state(state)
        return state

    def append_workspace_file(
        self,
        *,
        workspace_file: str,
        entries: Sequence[Mapping[str, Any]],
        vectors: Sequence[Sequence[Sequence[float]]],
    ) -> WorkspaceBuildState:
        if len(entries) != len(vectors):
            raise ValueError("entries and vectors must have the same length")

        metadata = self._require_metadata()
        state = self._require_workspace_state()
        raw_chunks = cast(dict[str, Any], metadata["chunks"])

        next_shard_id = state.next_shard_id
        documents_indexed = state.documents_indexed
        if entries:
            shard_name = f"{next_shard_id:08d}.faiss"
            shard_path = self._shards_dir / shard_name
            self._shards_dir.mkdir(parents=True, exist_ok=True)
            self._write_index_file(vectors, shard_path)
            for entry, matrix in zip(entries, vectors, strict=True):
                raw_chunks[str(entry["chunk_id"])] = {
                    "faiss_ids": list(range(len(matrix))),
                    "payload": dict(entry),
                    "shard": shard_name,
                }
            next_shard_id += 1
            documents_indexed += len(entries)

        completed_files = list(state.completed_files)
        if workspace_file not in completed_files:
            completed_files.append(workspace_file)
        failed_files = dict(state.failed_files)
        failed_files.pop(workspace_file, None)

        updated_state = WorkspaceBuildState(
            status="in_progress",
            workspace_signature=state.workspace_signature,
            model_name=state.model_name,
            indexed_at=state.indexed_at,
            total_files=state.total_files,
            completed_files=tuple(completed_files),
            failed_files=failed_files,
            documents_indexed=documents_indexed,
            next_shard_id=next_shard_id,
        )
        self._write_metadata(metadata)
        self._write_state(updated_state)
        return updated_state

    def mark_workspace_file_failed(
        self,
        *,
        workspace_file: str,
        error: str,
    ) -> WorkspaceBuildState:
        state = self._require_workspace_state()
        failed_files = dict(state.failed_files)
        failed_files[workspace_file] = error
        updated_state = WorkspaceBuildState(
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

    def complete_workspace_build(self) -> WorkspaceBuildState:
        state = self._require_workspace_state()
        final_status = "completed" if not state.failed_files else "partial"
        updated_state = WorkspaceBuildState(
            status=final_status,
            workspace_signature=state.workspace_signature,
            model_name=state.model_name,
            indexed_at=state.indexed_at,
            total_files=state.total_files,
            completed_files=state.completed_files,
            failed_files=state.failed_files,
            documents_indexed=state.documents_indexed,
            next_shard_id=state.next_shard_id,
        )
        self._write_state(updated_state)
        return updated_state

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
        if self._state_path.exists():
            self._state_path.unlink()

        flat_vectors: list[list[float]] = []
        chunks: dict[str, dict[str, Any]] = {}
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
            chunks[chunk_id] = {
                "faiss_ids": faiss_ids,
                "payload": dict(entry),
            }

        if flat_vectors:
            self._write_flat_index(flat_vectors, vector_size, self._vectors_path)
        elif self._vectors_path.exists():
            self._vectors_path.unlink()

        metadata = {
            "model": model_name,
            "indexed_at": indexed_at,
            "chunks": chunks,
        }
        self._write_metadata(metadata)

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

    def _load_metadata(self) -> dict[str, Any] | None:
        if not self._metadata_path.exists():
            if self._vectors_path.exists():
                raise MetadataCorruptionError("metadata.json is missing")
            return None
        try:
            return json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MetadataCorruptionError("metadata.json is not valid JSON") from exc

    def _require_metadata(self) -> dict[str, Any]:
        metadata = self._load_metadata()
        if metadata is None:
            raise MetadataCorruptionError("metadata.json is missing")
        if not isinstance(metadata.get("chunks"), dict):
            raise MetadataCorruptionError("metadata.json is missing the 'chunks' object")
        return metadata

    def _require_workspace_state(self) -> WorkspaceBuildState:
        state = self.load_workspace_state()
        if state is None:
            raise MetadataCorruptionError("state.json is missing")
        return state

    def _write_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._write_json(self._metadata_path, metadata)

    def _write_state(self, state: WorkspaceBuildState) -> None:
        payload = {
            "status": state.status,
            "workspace_signature": state.workspace_signature,
            "model_name": state.model_name,
            "indexed_at": state.indexed_at,
            "total_files": state.total_files,
            "completed_files": list(state.completed_files),
            "failed_files": state.failed_files,
            "documents_indexed": state.documents_indexed,
            "next_shard_id": state.next_shard_id,
        }
        self._write_json(self._state_path, payload)

    def _write_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _reset_index_dir(self) -> None:
        self._index_dir.mkdir(parents=True, exist_ok=True)
        if self._vectors_path.exists():
            self._vectors_path.unlink()
        if self._metadata_path.exists():
            self._metadata_path.unlink()
        if self._state_path.exists():
            self._state_path.unlink()
        self._clear_shards()

    def _clear_shards(self) -> None:
        if self._shards_dir.exists():
            shutil.rmtree(self._shards_dir)
