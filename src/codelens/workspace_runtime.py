from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def build_workspace_resolver_context(
    filepath: str | Path,
    *,
    workspace: GradleWorkspaceModel | None = None,
    workspace_json: str | Path | None = None,
    resolve_binaries: bool = False,
    jdk_home: str | Path | None = None,
    index_cache: IndexCache | None = None,
) -> WorkspaceResolverContext:
    cache = index_cache or DEFAULT_INDEX_CACHE
    workspace_cache_token = None
    if workspace is None:
        if workspace_json is None:
            raise ValueError("workspace or workspace_json is required")
        workspace_json_path = Path(workspace_json).resolve()
        workspace_cache_token = str(_file_signature(workspace_json_path))
        workspace = GradleWorkspaceModel.from_json_file(workspace_json_path)
        logger.info("Loaded workspace context from %s", workspace_json_path)

    file_path = Path(filepath).resolve()
    logger.info("Building workspace resolver context for %s", file_path)
    visible_roots = [
        Path(root)
        for root in workspace.visible_source_roots_for_file(file_path)
        if Path(root).exists()
    ]
    logger.info("Resolved %d visible source roots", len(visible_roots))
    source_index = cache.get_source_index(
        visible_roots,
        source_set_lookup=workspace.source_set_lookup,
        context_token=workspace_cache_token,
    )

    source_set_id = workspace.source_set_for_file(file_path)
    logger.info("Resolved source set: %s", source_set_id.key if source_set_id else "unmapped")
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

    resolved_jdk_home = Path(jdk_home) if jdk_home is not None else (
        Path(workspace.jdk_home) if workspace.jdk_home else None
    )
    jdk_index = None
    if resolved_jdk_home is not None and resolved_jdk_home.exists():
        logger.info("Using JDK home %s", resolved_jdk_home.resolve())
        jdk_index = cache.get_jdk_index(resolved_jdk_home)

    resolver = TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            file_path,
            source_index=source_index,
            binary_index=binary_index,
            jdk_index=jdk_index,
        )
    )
    return WorkspaceResolverContext(
        workspace=workspace,
        filepath=str(file_path),
        source_set_id=source_set_id,
        workspace_cache_token=workspace_cache_token,
        source_index=source_index,
        binary_index=binary_index,
        jdk_index=jdk_index,
        resolver=resolver,
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
    from .chunker import parse_java

    context = build_workspace_resolver_context(
        filepath,
        workspace=workspace,
        workspace_json=workspace_json,
        resolve_binaries=resolve_binaries,
        jdk_home=jdk_home,
        index_cache=index_cache,
    )
    code = Path(context.filepath).read_bytes()
    logger.info("Parsing Java file %s with workspace-aware resolver", context.filepath)
    chunks = parse_java(code, filepath=context.filepath, resolver=context.resolver)
    return chunks, context
