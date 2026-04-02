from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from codelens.chunker import parse_java
    from codelens.indexing.service import FaissIndexingService
    from codelens.retrieval.search import RetrievalSearchService
    from codelens.retrieval.service import RetrievalIndexingService
    from codelens.workspace_runtime import parse_java_file_with_workspace

__all__ = [
    "FaissIndexingService",
    "parse_java",
    "parse_java_file_with_workspace",
    "RetrievalIndexingService",
    "RetrievalSearchService",
]


@overload
def __getattr__(
    name: Literal["FaissIndexingService"],
) -> type[FaissIndexingService]: ...


@overload
def __getattr__(name: Literal["parse_java"]): ...


@overload
def __getattr__(name: Literal["parse_java_file_with_workspace"]): ...


@overload
def __getattr__(
    name: Literal["RetrievalIndexingService"],
) -> type[RetrievalIndexingService]: ...


@overload
def __getattr__(
    name: Literal["RetrievalSearchService"],
) -> type[RetrievalSearchService]: ...


def __getattr__(name: str) -> Any:
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
    if name == "RetrievalSearchService":
        from codelens.retrieval.search import RetrievalSearchService

        return RetrievalSearchService
    raise AttributeError(name)
