from __future__ import annotations

import logging
import threading
from typing import Protocol, Sequence

from codelens.logging_config import get_logger, log_event

logger = get_logger(__name__)


class LateInteractionEncoder(Protocol):
    @property
    def model_name(self) -> str: ...

    def prepare(self) -> None: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]: ...


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
        self._document_batches_encoded = 0
        self._query_batches_encoded = 0

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed_documents(self, texts: Sequence[str]) -> list[list[list[float]]]:
        model = self._get_model()
        first_batch = self._document_batches_encoded == 0
        if first_batch:
            log_event(
                logger,
                level=logging.INFO,
                message="Starting document embedding",
                model=self._model_name,
                batch_size=len(texts),
                device=self._device or "default",
            )
        embeddings = model.encode(
            list(texts),
            is_query=False,
            show_progress_bar=False,
        )
        self._document_batches_encoded += 1
        if first_batch:
            log_event(
                logger,
                level=logging.INFO,
                message="Finished document embedding",
                model=self._model_name,
                batch_size=len(texts),
                embeddings=len(embeddings),
            )
        return [embedding.tolist() for embedding in embeddings]

    def prepare(self) -> None:
        self._get_model()

    def embed_queries(self, texts: Sequence[str]) -> list[list[list[float]]]:
        model = self._get_model()
        first_batch = self._query_batches_encoded == 0
        if first_batch:
            log_event(
                logger,
                level=logging.INFO,
                message="Starting query embedding",
                model=self._model_name,
                batch_size=len(texts),
                device=self._device or "default",
            )
        embeddings = model.encode(
            list(texts),
            is_query=True,
            show_progress_bar=False,
        )
        self._query_batches_encoded += 1
        if first_batch:
            log_event(
                logger,
                level=logging.INFO,
                message="Finished query embedding",
                model=self._model_name,
                batch_size=len(texts),
                embeddings=len(embeddings),
            )
        return [embedding.tolist() for embedding in embeddings]

    def close(self) -> None:
        self._model = None

    def _get_model(self):
        if self._model is None:
            _configure_tqdm_lock()
            log_event(
                logger,
                level=logging.INFO,
                message="Loading ColBERT model",
                model=self._model_name,
                device=self._device or "default",
                query_length=self._query_length,
                document_length=self._document_length,
            )
            try:
                from pylate import models
            except ImportError as exc:
                raise RuntimeError("pylate is required for ColBERT encoding") from exc
            self._model = models.ColBERT(
                model_name_or_path=self._model_name,
                device=self._device,
                query_length=self._query_length,
                document_length=self._document_length,
            )
            log_event(
                logger,
                level=logging.INFO,
                message="Loaded ColBERT model",
                model=self._model_name,
                device=self._device or "default",
            )
        return self._model


def _configure_tqdm_lock() -> None:
    try:
        from tqdm import tqdm
    except ImportError:
        return

    lock = getattr(tqdm, "_lock", None)
    if lock is not None and type(lock).__name__ != "TqdmDefaultWriteLock":
        return

    tqdm.set_lock(threading.RLock())
