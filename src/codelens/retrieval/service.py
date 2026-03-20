from __future__ import annotations

from pathlib import Path

from codelens.chunker import parse_java
from codelens.db.session import get_session
from codelens.repository.retrieval_document_repo import upsert_documents
from codelens.retrieval.documents import RetrievalDocument, build_retrieval_documents
from codelens.type_resolver import TypeResolver
from codelens.workspace_runtime import parse_java_file_with_workspace


class RetrievalIndexingService:
    def index_chunks(
        self, chunks: list[dict], *, source_set: str | None = None
    ) -> list[RetrievalDocument]:
        documents = build_retrieval_documents(chunks, source_set=source_set)
        with get_session() as session:
            upsert_documents(session, documents)
        return documents

    def index_java(
        self,
        code: bytes,
        *,
        filepath: str | None = None,
        resolver: TypeResolver | None = None,
        source_set: str | None = None,
    ) -> list[RetrievalDocument]:
        chunks = parse_java(code, filepath=filepath, resolver=resolver)
        return self.index_chunks(chunks, source_set=source_set)

    def index_java_file_with_workspace(
        self,
        filepath: str | Path,
        *,
        workspace_json: str | Path,
        resolve_binaries: bool = False,
        jdk_home: str | Path | None = None,
    ) -> list[RetrievalDocument]:
        chunks, context = parse_java_file_with_workspace(
            filepath,
            workspace_json=workspace_json,
            resolve_binaries=resolve_binaries,
            jdk_home=jdk_home,
        )
        source_set = context.source_set_id.key if context.source_set_id else None
        return self.index_chunks(chunks, source_set=source_set)
