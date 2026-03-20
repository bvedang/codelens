from __future__ import annotations

__all__ = [
    "FaissIndexingService",
    "parse_java",
    "parse_java_file_with_workspace",
    "RetrievalIndexingService",
]


def __getattr__(name: str):
    if name == "FaissIndexingService":
        from codelens.indexing.service import FaissIndexingService

        return FaissIndexingService
    if name == "parse_java":
        from codelens.chunker import parse_java

        return parse_java
    if name == "parse_java_file_with_workspace":
        from codelens.workspace_runtime import parse_java_file_with_workspace

        return parse_java_file_with_workspace
    if name == "RetrievalIndexingService":
        from codelens.retrieval.service import RetrievalIndexingService

        return RetrievalIndexingService
    raise AttributeError(name)
