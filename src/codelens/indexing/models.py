from dataclasses import dataclass
from typing import Any


class MetadataCorruptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    faiss_ids: tuple[int, ...]
    payload: dict[str, Any]
    shard: str | None = None


@dataclass(frozen=True)
class LoadedIndex:
    model_name: str | None
    indexed_at: str | None
    vector_size: int
    index: Any | None
    chunks: dict[str, StoredChunk]


@dataclass(frozen=True)
class IndexStatus:
    chunk_count: int
    indexed_at: str | None
    model_name: str | None


@dataclass(frozen=True)
class WorkspaceBuildState:
    status: str
    workspace_signature: str
    model_name: str
    indexed_at: str
    total_files: int
    completed_files: tuple[str, ...]
    failed_files: dict[str, str]
    documents_indexed: int
    next_shard_id: int
