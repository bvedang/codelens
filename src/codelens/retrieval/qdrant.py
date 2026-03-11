from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from codelens.logging_config import get_logger
from codelens.retrieval.documents import RetrievalDocument

logger = get_logger(__name__)


@dataclass(frozen=True)
class QdrantConfig:
    collection_name: str = "codelens_chunks"
    url: str = "http://127.0.0.1:6333"
    api_key: str | None = None
    prefer_grpc: bool = False


class LateInteractionEncoder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]:
        ...


class FastEmbedColbertEncoder:
    def __init__(self, model_name: str = "colbert-ir/colbertv2.0") -> None:
        self._model_name = model_name
        self._embedding_model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]:
        model = self._get_model()
        embeddings = []
        for matrix in model.embed(texts):
            embeddings.append([list(row) for row in matrix.tolist()])
        return embeddings

    def _get_model(self):
        if self._embedding_model is None:
            try:
                from fastembed import LateInteractionTextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    "fastembed is required for ColBERT document encoding"
                ) from exc
            self._embedding_model = LateInteractionTextEmbedding(model_name=self._model_name)
        return self._embedding_model


class QdrantDocumentRepository:
    def __init__(self, config: QdrantConfig, client=None) -> None:
        self._config = config
        self._client = client

    @property
    def collection_name(self) -> str:
        return self._config.collection_name

    def ensure_collection(self, vector_size: int) -> None:
        client = self._get_client()
        if client.collection_exists(self.collection_name):
            return

        models = self._models()
        logger.info(
            "Creating Qdrant collection %s with vector size %d",
            self.collection_name,
            vector_size,
        )
        client.create_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
                multivector_config=models.MultiVectorConfig(
                    comparator=models.MultiVectorComparator.MAX_SIM,
                ),
            ),
        )

    def upsert_documents(
        self,
        documents: Sequence[RetrievalDocument],
        *,
        vectors: Sequence[Sequence[Sequence[float]]],
    ) -> None:
        if not documents:
            return
        client = self._get_client()
        models = self._models()
        points = []
        for document, vector in zip(documents, vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=document.chunk_id,
                    vector=[list(row) for row in vector],
                    payload=self._payload(document),
                )
            )
        client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    def delete_by_repo(self, repo_root: str) -> None:
        self._delete_with_filter(
            must=[self._field_match("repo_root", repo_root)]
        )

    def delete_by_file(self, repo_root: str, filepath: str) -> None:
        self._delete_with_filter(
            must=[
                self._field_match("repo_root", repo_root),
                self._field_match("filepath", filepath),
            ]
        )

    def _delete_with_filter(self, *, must: list) -> None:
        client = self._get_client()
        models = self._models()
        client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=must)
            ),
            wait=True,
        )

    def _field_match(self, key: str, value: str):
        models = self._models()
        return models.FieldCondition(
            key=key,
            match=models.MatchValue(value=value),
        )

    def _payload(self, document: RetrievalDocument) -> dict:
        return {
            "chunk_id": document.chunk_id,
            "kind": document.kind,
            "name": document.name,
            "filepath": document.filepath,
            "repo_root": document.repo_root,
            "package_name": document.package_name,
            "owner_chain": list(document.owner_chain),
            "source_set": document.source_set,
            "signature": document.signature,
            "return_type": document.return_type,
            "field_type": document.field_type,
            "component_type": document.component_type,
            "annotations": list(document.annotations),
            "modifiers": list(document.modifiers),
            "calls": list(document.calls),
            "fields_accessed": list(document.fields_accessed),
            "throws": list(document.throws),
            "extends_name": document.extends_name,
            "implements": list(document.implements),
            "permits": list(document.permits),
            "resolved_symbols": list(document.resolved_symbols),
            "text": document.text,
            "retrieval_text": document.retrieval_text,
        }

    def _get_client(self):
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RuntimeError(
                    "qdrant-client is required for Qdrant indexing"
                ) from exc
            self._client = QdrantClient(
                url=self._config.url,
                api_key=self._config.api_key,
                prefer_grpc=self._config.prefer_grpc,
            )
        return self._client

    def _models(self):
        from qdrant_client import models

        return models


def repo_root_for_path(path: str | Path) -> str:
    return str(Path(path).resolve())
