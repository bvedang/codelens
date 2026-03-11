from __future__ import annotations

import json
from dataclasses import asdict

from codelens.retrieval.db import SQLiteConfig, connect_sqlite
from codelens.retrieval.documents import RetrievalDocument

TABLE_NAME = "retrieval_documents"
JSON_COLUMN_MAP = {
    "owner_chain_json": "owner_chain",
    "annotations_json": "annotations",
    "modifiers_json": "modifiers",
    "calls_json": "calls",
    "fields_accessed_json": "fields_accessed",
    "throws_json": "throws",
    "implements_json": "implements",
    "permits_json": "permits",
    "resolved_symbols_json": "resolved_symbols",
}
SCALAR_COLUMNS = (
    "chunk_id",
    "kind",
    "name",
    "filepath",
    "repo_root",
    "package_name",
    "source_set",
    "signature",
    "return_type",
    "field_type",
    "component_type",
    "extends_name",
    "text",
    "retrieval_text",
)
ALL_COLUMNS = (
    "chunk_id",
    "kind",
    "name",
    "filepath",
    "repo_root",
    "package_name",
    "owner_chain_json",
    "source_set",
    "signature",
    "return_type",
    "field_type",
    "component_type",
    "annotations_json",
    "modifiers_json",
    "calls_json",
    "fields_accessed_json",
    "throws_json",
    "extends_name",
    "implements_json",
    "permits_json",
    "resolved_symbols_json",
    "text",
    "retrieval_text",
)
CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    chunk_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT,
    filepath TEXT,
    repo_root TEXT,
    package_name TEXT,
    owner_chain_json TEXT NOT NULL,
    source_set TEXT,
    signature TEXT,
    return_type TEXT,
    field_type TEXT,
    component_type TEXT,
    annotations_json TEXT NOT NULL,
    modifiers_json TEXT NOT NULL,
    calls_json TEXT NOT NULL,
    fields_accessed_json TEXT NOT NULL,
    throws_json TEXT NOT NULL,
    extends_name TEXT,
    implements_json TEXT NOT NULL,
    permits_json TEXT NOT NULL,
    resolved_symbols_json TEXT NOT NULL,
    text TEXT NOT NULL,
    retrieval_text TEXT NOT NULL
)
"""
INDEX_COLUMNS = ("kind", "name", "filepath", "repo_root")
UPSERT_SQL = f"""
INSERT INTO {TABLE_NAME} (
    {", ".join(ALL_COLUMNS)}
) VALUES (
    {", ".join(f":{column}" for column in ALL_COLUMNS)}
)
ON CONFLICT(chunk_id) DO UPDATE SET
    {", ".join(f"{column} = excluded.{column}" for column in ALL_COLUMNS if column != "chunk_id")}
"""


class RetrievalDocumentRepository:
    def __init__(self, config: SQLiteConfig) -> None:
        self._config = config

    def initialize(self) -> None:
        with connect_sqlite(self._config) as connection:
            connection.execute(CREATE_TABLE_SQL)
            for column in INDEX_COLUMNS:
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_{column} ON {TABLE_NAME}({column})"
                )

    def upsert_documents(self, documents: list[RetrievalDocument]) -> None:
        if not documents:
            return
        with connect_sqlite(self._config) as connection:
            connection.executemany(
                UPSERT_SQL,
                [self._serialize(document) for document in documents],
            )

    def get_document(self, chunk_id: str) -> RetrievalDocument | None:
        with connect_sqlite(self._config) as connection:
            row = connection.execute(
                f"SELECT * FROM {TABLE_NAME} WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        if row is None:
            return None
        return self._deserialize(row)

    def list_documents(
        self,
        *,
        repo_root: str | None = None,
        kind: str | None = None,
        filepath: str | None = None,
    ) -> list[RetrievalDocument]:
        filters = []
        params: list[str] = []
        if repo_root is not None:
            filters.append("repo_root = ?")
            params.append(repo_root)
        if kind is not None:
            filters.append("kind = ?")
            params.append(kind)
        if filepath is not None:
            filters.append("filepath = ?")
            params.append(filepath)

        query = f"SELECT * FROM {TABLE_NAME}"
        if filters:
            query += f" WHERE {' AND '.join(filters)}"
        query += " ORDER BY chunk_id"

        with connect_sqlite(self._config) as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._deserialize(row) for row in rows]

    def delete_by_repo(self, repo_root: str) -> None:
        with connect_sqlite(self._config) as connection:
            connection.execute(
                f"DELETE FROM {TABLE_NAME} WHERE repo_root = ?",
                (repo_root,),
            )

    def delete_by_file(self, repo_root: str, filepath: str) -> None:
        with connect_sqlite(self._config) as connection:
            connection.execute(
                f"DELETE FROM {TABLE_NAME} WHERE repo_root = ? AND filepath = ?",
                (repo_root, filepath),
            )

    def _serialize(self, document: RetrievalDocument) -> dict:
        payload = asdict(document)
        record = {column: payload[column] for column in SCALAR_COLUMNS}
        for column, field_name in JSON_COLUMN_MAP.items():
            record[column] = json.dumps(payload[field_name])
        return record

    def _deserialize(self, row) -> RetrievalDocument:
        payload = {column: row[column] for column in SCALAR_COLUMNS}
        for column, field_name in JSON_COLUMN_MAP.items():
            payload[field_name] = tuple(json.loads(row[column]))
        return RetrievalDocument(**payload)
