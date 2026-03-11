from __future__ import annotations

from typing import Protocol, Sequence


class LateInteractionEncoder(Protocol):
    @property
    def model_name(self) -> str:
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]:
        ...


class ColbertEncoder:
    def __init__(
        self,
        model_name: str = "lightonai/ColBERT-Zero",
        *,
        device: str | None = None,
        query_length: int = 32,
        document_length: int = 8192,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._query_length = query_length
        self._document_length = document_length
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]:
        model = self._get_model()
        embeddings = model.encode(list(texts), is_query=False)
        return [embedding.tolist() for embedding in embeddings]

    def embed_queries(self, texts: Sequence[str]) -> list[list[list[float]]]:
        model = self._get_model()
        embeddings = model.encode(list(texts), is_query=True)
        return [embedding.tolist() for embedding in embeddings]

    def _get_model(self):
        if self._model is None:
            try:
                from pylate import models
            except ImportError as exc:
                raise RuntimeError(
                    "pylate is required for ColBERT encoding"
                ) from exc
            self._model = models.ColBERT(
                model_name_or_path=self._model_name,
                device=self._device,
                query_length=self._query_length,
                document_length=self._document_length,
            )
        return self._model


FastEmbedColbertEncoder = ColbertEncoder
