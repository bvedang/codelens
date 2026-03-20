from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codelens.gradle_model import GradleWorkspaceModel, SourceSetId
from codelens.index_cache import IndexCache, _file_signature
from codelens.logging_config import get_logger
from codelens.symbol_index import SymbolIndex
from codelens.type_resolver import TypeResolver

DEFAULT_INDEX_CACHE = IndexCache()
logger = get_logger(__name__)


@dataclass(frozen=True)
class WorkspaceResolverContext:
    workspace: GradleWorkspaceModel
    filepath: str
    source_set_id: SourceSetId | None
    workspace_cache_token: str | None
    source_index: SymbolIndex
    binary_index: SymbolIndex | None
    jdk_index: SymbolIndex | None
    resolver: TypeResolver


@dataclass(frozen=True)
class WorkspaceSourceSetContext:
    workspace: GradleWorkspaceModel
    source_set_id: SourceSetId | None
    workspace_cache_token: str | None
    source_index: SymbolIndex
    binary_index: SymbolIndex | None
    jdk_index: SymbolIndex | None
    resolver: TypeResolver


@dataclass(frozen=True)
class WorkspaceSharedContext:
    workspace: GradleWorkspaceModel
    workspace_cache_token: str | None
    source_index: SymbolIndex
    jdk_index: SymbolIndex | None


WorkspaceParseContext = WorkspaceSourceSetContext | WorkspaceResolverContext
ParsedChunk = dict[str, object]


def build_workspace_shared_context(
    *,
    workspace: GradleWorkspaceModel | None = None,
    workspace_json: str | Path | None = None,
    jdk_home: str | Path | None = None,
    index_cache: IndexCache | None = None,
) -> WorkspaceSharedContext:
    cache = index_cache or DEFAULT_INDEX_CACHE
    workspace, workspace_cache_token = _resolve_workspace_inputs(
        workspace=workspace,
        workspace_json=workspace_json,
    )

    all_roots = sorted(
        {
            str(Path(root).resolve())
            for source_set in workspace.source_sets.values()
            for root in source_set.all_roots
            if Path(root).exists()
        }
    )
    logger.info("Building workspace-wide source index for %d roots", len(all_roots))
    source_index = cache.get_source_index(
        [Path(root) for root in all_roots],
        source_set_lookup=workspace.source_set_lookup,
        context_token=workspace_cache_token,
    )

    resolved_jdk_home = (
        Path(jdk_home)
        if jdk_home is not None
        else (Path(workspace.jdk_home) if workspace.jdk_home else None)
    )
    jdk_index = None
    if resolved_jdk_home is not None and resolved_jdk_home.exists():
        logger.info("Using JDK home %s", resolved_jdk_home.resolve())
        jdk_index = cache.get_jdk_index(resolved_jdk_home)

    return WorkspaceSharedContext(
        workspace=workspace,
        workspace_cache_token=workspace_cache_token,
        source_index=source_index,
        jdk_index=jdk_index,
    )


def build_workspace_source_set_context(
    source_set_id: SourceSetId | None,
    *,
    workspace: GradleWorkspaceModel | None = None,
    workspace_json: str | Path | None = None,
    resolve_binaries: bool = False,
    jdk_home: str | Path | None = None,
    index_cache: IndexCache | None = None,
    shared_context: WorkspaceSharedContext | None = None,
) -> WorkspaceSourceSetContext:
    cache = index_cache or DEFAULT_INDEX_CACHE
    if shared_context is None:
        shared_context = build_workspace_shared_context(
            workspace=workspace,
            workspace_json=workspace_json,
            jdk_home=jdk_home,
            index_cache=cache,
        )
    workspace = shared_context.workspace
    workspace_cache_token = shared_context.workspace_cache_token

    logger.info(
        "Building workspace resolver context for source set %s",
        source_set_id.key if source_set_id else "unmapped",
    )
    visible_roots = [
        Path(root)
        for root in (
            workspace.visible_source_roots(source_set_id) if source_set_id else ()
        )
        if Path(root).exists()
    ]
    logger.info("Resolved %d visible source roots", len(visible_roots))
    source_index = shared_context.source_index

    logger.info(
        "Resolved source set: %s", source_set_id.key if source_set_id else "unmapped"
    )
    binary_index = None
    if resolve_binaries and source_set_id is not None:
        binary_paths = [
            Path(entry)
            for entry in workspace.visible_external_binary_entries(source_set_id)
            if Path(entry).exists()
        ]
        if binary_paths:
            logger.info("Resolved %d visible binary paths", len(binary_paths))
            binary_index = cache.get_binary_index(binary_paths)

    jdk_index = shared_context.jdk_index

    resolver = TypeResolver(
        type_index=workspace.visible_type_index(
            source_set_id,
            source_index=source_index,
            binary_index=binary_index,
            jdk_index=jdk_index,
        )
    )
    return WorkspaceSourceSetContext(
        workspace=workspace,
        source_set_id=source_set_id,
        workspace_cache_token=workspace_cache_token,
        source_index=source_index,
        binary_index=binary_index,
        jdk_index=jdk_index,
        resolver=resolver,
    )


def build_workspace_resolver_context(
    filepath: str | Path,
    *,
    workspace: GradleWorkspaceModel | None = None,
    workspace_json: str | Path | None = None,
    resolve_binaries: bool = False,
    jdk_home: str | Path | None = None,
    index_cache: IndexCache | None = None,
) -> WorkspaceResolverContext:
    file_path = Path(filepath).resolve()
    workspace, _ = _resolve_workspace_inputs(
        workspace=workspace,
        workspace_json=workspace_json,
    )
    logger.info("Building workspace resolver context for %s", file_path)
    shared_context = build_workspace_source_set_context(
        workspace.source_set_for_file(file_path),
        workspace=workspace,
        workspace_json=workspace_json,
        resolve_binaries=resolve_binaries,
        jdk_home=jdk_home,
        index_cache=index_cache,
    )
    return WorkspaceResolverContext(
        workspace=shared_context.workspace,
        filepath=str(file_path),
        source_set_id=shared_context.source_set_id,
        workspace_cache_token=shared_context.workspace_cache_token,
        source_index=shared_context.source_index,
        binary_index=shared_context.binary_index,
        jdk_index=shared_context.jdk_index,
        resolver=shared_context.resolver,
    )


def parse_java_file_with_resolver_context(
    filepath: str | Path,
    *,
    context: WorkspaceParseContext,
) -> tuple[list[ParsedChunk], WorkspaceResolverContext]:
    from .chunker import parse_java

    file_path = Path(filepath).resolve()
    code = file_path.read_bytes()
    logger.info("Parsing Java file %s with workspace-aware resolver", file_path)
    chunks = cast(
        list[ParsedChunk],
        parse_java(code, filepath=str(file_path), resolver=context.resolver),
    )
    return chunks, WorkspaceResolverContext(
        workspace=context.workspace,
        filepath=str(file_path),
        source_set_id=context.source_set_id,
        workspace_cache_token=context.workspace_cache_token,
        source_index=context.source_index,
        binary_index=context.binary_index,
        jdk_index=context.jdk_index,
        resolver=context.resolver,
    )


def parse_java_file_with_workspace(
    filepath: str | Path,
    *,
    workspace: GradleWorkspaceModel | None = None,
    workspace_json: str | Path | None = None,
    resolve_binaries: bool = False,
    jdk_home: str | Path | None = None,
    index_cache: IndexCache | None = None,
):
    context = build_workspace_resolver_context(
        filepath,
        workspace=workspace,
        workspace_json=workspace_json,
        resolve_binaries=resolve_binaries,
        jdk_home=jdk_home,
        index_cache=index_cache,
    )
    return parse_java_file_with_resolver_context(filepath, context=context)


def _resolve_workspace_inputs(
    *,
    workspace: GradleWorkspaceModel | None,
    workspace_json: str | Path | None,
) -> tuple[GradleWorkspaceModel, str | None]:
    workspace_cache_token = None
    if workspace_json is not None:
        workspace_json_path = Path(workspace_json).resolve()
        workspace_cache_token = str(_file_signature(workspace_json_path))
        if workspace is not None:
            return workspace, workspace_cache_token
        loaded_workspace = GradleWorkspaceModel.from_json_file(workspace_json_path)
        logger.info("Loaded workspace context from %s", workspace_json_path)
        return loaded_workspace, workspace_cache_token
    if workspace is not None:
        return workspace, workspace_cache_token
    if workspace_json is None:
        raise ValueError("workspace or workspace_json is required")
    raise AssertionError("unreachable")
