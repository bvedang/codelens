from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path

INDEXABLE_KINDS = {
    "method",
    "constructor",
    "field",
    "skeleton",
    "behavior",
    "type",
    "record_component",
}
POSITIONAL_KINDS = {"behavior", "skeleton"}
MIN_SOURCE_LENGTH = 20
SKELETON_SEGMENT_MAX_CHARS = 4096

ChunkData = Mapping[str, object]


@dataclass(frozen=True)
class IndexDocument:
    chunk_id: str
    repo_root: str
    file_path: str
    chunk_kind: str
    owner_chain: tuple[str, ...]
    package: str | None
    name: str | None
    start_line: int | None
    end_line: int | None
    start_col: int | None
    end_col: int | None
    signature: str | None
    return_type: str | None
    field_type: str | None
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    resolved_calls: tuple[str, ...]
    fields_accessed: tuple[str, ...]
    throws: tuple[str, ...]
    implements: tuple[str, ...]
    extends: str | None
    source_set: str | None
    retrieval_text: str
    source_text: str
    indexed_at: str


def build_index_documents(
    chunks: Iterable[ChunkData],
    *,
    repo_root: str | Path,
    indexed_at: str,
    source_set: str | None = None,
) -> list[IndexDocument]:
    repo_root_path = Path(repo_root).resolve()
    repo_root_str = str(repo_root_path)
    chunk_list = list(chunks)
    file_chunk = next(
        (chunk for chunk in chunk_list if chunk.get("kind") == "file"), None
    )
    package_name = _package_name(file_chunk)
    positions: dict[str, int] = {}
    documents: list[IndexDocument] = []

    for chunk in chunk_list:
        kind = chunk.get("kind")
        if kind not in INDEXABLE_KINDS:
            continue

        source_text = str(chunk.get("text") or "").strip()
        if len(source_text) < MIN_SOURCE_LENGTH:
            continue

        filepath = _optional_str(chunk.get("filepath"))
        if not filepath:
            continue

        relative_path = _relative_file_path(filepath, repo_root_path)
        segments = _chunk_source_segments(kind=str(kind), source_text=source_text)
        for segment in segments:
            position = positions.get(str(kind), 0)
            positions[str(kind)] = position + 1
            documents.append(
                build_index_document(
                    chunk,
                    repo_root=repo_root_str,
                    file_path=relative_path,
                    package_name=package_name,
                    indexed_at=indexed_at,
                    source_set=source_set,
                    position=position,
                    source_text=segment.text,
                    segment_label=segment.label,
                    declaration_override=segment.declaration,
                )
            )

    return documents


def build_index_document(
    chunk: ChunkData,
    *,
    repo_root: str,
    file_path: str,
    package_name: str | None,
    indexed_at: str,
    source_set: str | None,
    position: int,
    source_text: str | None = None,
    segment_label: str | None = None,
    declaration_override: str | None = None,
) -> IndexDocument:
    chunk_kind = str(chunk["kind"])
    owner_chain = tuple(_string_list(chunk.get("owner_chain")))
    name = _chunk_name(chunk, chunk_kind)
    annotations = tuple(_annotation_texts(chunk))
    modifiers = tuple(str(item) for item in chunk.get("modifiers", ()))
    resolved_calls = tuple(str(item) for item in chunk.get("calls", ()))
    fields_accessed = tuple(str(item) for item in chunk.get("fields_accessed", ()))
    throws = tuple(str(item) for item in chunk.get("throws", ()))
    implements = _split_clause(chunk.get("implements"), "implements")
    extends_name = _strip_clause_prefix(chunk.get("extends"), "extends")
    source_text = (
        source_text if source_text is not None else str(chunk.get("text") or "").strip()
    )
    field_type = chunk.get("field_type") or chunk.get("component_type")
    signature = _build_signature(chunk)
    retrieval_label = _retrieval_label(
        package_name,
        owner_chain,
        name,
        chunk_kind,
    )
    retrieval_text = _build_retrieval_text(
        chunk_kind=chunk_kind,
        label=retrieval_label,
        declaration=declaration_override
        or _declaration_text(
            chunk_kind=chunk_kind,
            source_text=source_text,
            signature=signature,
        ),
        annotations=annotations,
        source_text=source_text,
        segment_label=segment_label,
    )

    return IndexDocument(
        chunk_id=build_chunk_id(
            repo_root=repo_root,
            file_path=file_path,
            chunk_kind=chunk_kind,
            package_name=package_name,
            owner_chain=owner_chain,
            name=name,
            parameters=_optional_str(chunk.get("parameters")),
            position=position,
        ),
        repo_root=repo_root,
        file_path=file_path,
        chunk_kind=chunk_kind,
        owner_chain=owner_chain,
        package=package_name,
        name=name,
        start_line=_optional_int(chunk.get("start_line")),
        end_line=_optional_int(chunk.get("end_line")),
        start_col=_optional_int(chunk.get("start_col")),
        end_col=_optional_int(chunk.get("end_col")),
        signature=signature,
        return_type=_optional_str(chunk.get("return_type")),
        field_type=field_type,
        annotations=annotations,
        modifiers=modifiers,
        resolved_calls=resolved_calls,
        fields_accessed=fields_accessed,
        throws=throws,
        implements=implements,
        extends=extends_name,
        source_set=source_set,
        retrieval_text=retrieval_text,
        source_text=source_text,
        indexed_at=indexed_at,
    )


def build_chunk_id(
    *,
    repo_root: str,
    file_path: str,
    chunk_kind: str,
    package_name: str | None,
    owner_chain: tuple[str, ...],
    name: str | None,
    parameters: str | None,
    position: int,
) -> str:
    repo_hash = sha1(str(Path(repo_root).resolve()).encode("utf-8")).hexdigest()[:12]
    if chunk_kind in POSITIONAL_KINDS or not name:
        suffix = str(position)
    else:
        suffix = _symbol_suffix(
            chunk_kind=chunk_kind,
            package_name=package_name,
            owner_chain=owner_chain,
            name=name,
            parameters=parameters,
        )
    return f"{repo_hash}:{file_path}:{chunk_kind}:{suffix}"


def document_payload(document: IndexDocument) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": document.chunk_id,
        "repo_root": document.repo_root,
        "file_path": document.file_path,
        "chunk_kind": document.chunk_kind,
        "owner_chain": list(document.owner_chain),
        "package": document.package,
        "name": document.name,
        "start_line": document.start_line,
        "end_line": document.end_line,
        "start_col": document.start_col,
        "end_col": document.end_col,
        "signature": document.signature,
        "return_type": document.return_type,
        "field_type": document.field_type,
        "annotations": list(document.annotations),
        "modifiers": list(document.modifiers),
        "resolved_calls": list(document.resolved_calls),
        "fields_accessed": list(document.fields_accessed),
        "throws": list(document.throws),
        "implements": list(document.implements),
        "extends": document.extends,
        "source_set": document.source_set,
        "retrieval_text": document.retrieval_text,
        "source_text": document.source_text,
        "indexed_at": document.indexed_at,
    }
    return {
        key: value for key, value in payload.items() if value not in (None, "", [], ())
    }


def _package_name(file_chunk: ChunkData | None) -> str | None:
    if not file_chunk:
        return None
    package_decl = file_chunk.get("package")
    if not package_decl:
        return None
    return str(package_decl).replace("package ", "", 1).rstrip(";").strip()


def _relative_file_path(filepath: str | Path, repo_root: Path) -> str:
    return Path(filepath).resolve().relative_to(repo_root).as_posix()


def _chunk_name(chunk: ChunkData, chunk_kind: str) -> str | None:
    if chunk_kind == "constructor" and chunk.get("owner_chain"):
        owner_chain = _string_list(chunk.get("owner_chain"))
        return _optional_str(chunk.get("name")) or (
            owner_chain[-1] if owner_chain else None
        )
    if chunk_kind == "behavior":
        return None
    return _optional_str(chunk.get("name"))


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return None


def _annotation_texts(chunk: ChunkData) -> list[str]:
    values: list[str] = []
    for item in _object_list(chunk.get("annotations")):
        if isinstance(item, Mapping):
            text = item.get("text")
            if text:
                values.append(str(text))
            continue
        values.append(str(item))
    return values


def _build_signature(chunk: ChunkData) -> str | None:
    kind = chunk.get("kind")
    name = _optional_str(chunk.get("name"))
    parameters = _optional_str(chunk.get("parameters"))
    modifiers = " ".join(_string_list(chunk.get("modifiers")))
    prefix = f"{modifiers} " if modifiers else ""
    throws = " ".join(_string_list(chunk.get("throws")))
    throws_clause = f" throws {throws}" if throws else ""

    if kind == "method":
        return_type = _optional_str(chunk.get("return_type"))
        if name and parameters and return_type:
            return f"{prefix}{return_type} {name}{parameters}{throws_clause}".strip()
    if kind == "constructor" and name and parameters:
        return f"{prefix}{name}{parameters}{throws_clause}".strip()
    return None


def _split_clause(value: str | None, keyword: str) -> tuple[str, ...]:
    if not value:
        return ()
    cleaned = value.replace(f"{keyword} ", "", 1).strip()
    return tuple(part.strip() for part in cleaned.split(",") if part.strip())


def _strip_clause_prefix(value: str | None, keyword: str) -> str | None:
    if not value:
        return None
    return value.replace(f"{keyword} ", "", 1).strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _object_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _string_list(value: object) -> list[str]:
    return [str(item) for item in _object_list(value)]


def _qualified_name(
    package_name: str | None, owner_chain: tuple[str, ...], name: str
) -> str:
    parts = []
    if package_name:
        parts.append(package_name)
    parts.extend(owner_chain)
    parts.append(name)
    return ".".join(part for part in parts if part)


def _symbol_suffix(
    *,
    chunk_kind: str,
    package_name: str | None,
    owner_chain: tuple[str, ...],
    name: str,
    parameters: str | None,
) -> str:
    qualified_name = _qualified_name(package_name, owner_chain, name)
    if chunk_kind in {"method", "constructor"} and parameters:
        return f"{qualified_name}{_normalize_parameters(parameters)}"
    return qualified_name


def _normalize_parameters(parameters: str) -> str:
    return "".join(parameters.split())


def _retrieval_label(
    package_name: str | None,
    owner_chain: tuple[str, ...],
    name: str | None,
    chunk_kind: str,
) -> str | None:
    if chunk_kind == "behavior":
        parts = []
        if package_name:
            parts.append(package_name)
        parts.extend(owner_chain)
        return ".".join(parts) if parts else None
    if name is None:
        return None
    return _qualified_name(package_name, owner_chain, name)


def _declaration_text(
    *,
    chunk_kind: str,
    source_text: str,
    signature: str | None,
) -> str | None:
    if signature:
        return signature
    if chunk_kind == "behavior":
        return None
    if chunk_kind == "skeleton":
        for line in source_text.splitlines():
            candidate = line.strip()
            if candidate and not candidate.startswith("@"):
                return candidate
        return None
    if "{" in source_text:
        return source_text.split("{", 1)[0].strip()
    return source_text.strip() or None


def _build_retrieval_text(
    *,
    chunk_kind: str,
    label: str | None,
    declaration: str | None,
    annotations: tuple[str, ...],
    source_text: str,
    segment_label: str | None,
) -> str:
    parts = [f"[{chunk_kind}] {label}".strip()]
    if declaration:
        parts.append(declaration)
    if segment_label:
        parts.append(segment_label)
    if annotations:
        parts.append(" ".join(annotations))
    parts.append(source_text)
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class _ChunkSegment:
    text: str
    label: str | None = None
    declaration: str | None = None


def _chunk_source_segments(*, kind: str, source_text: str) -> list[_ChunkSegment]:
    if kind != "skeleton" or len(source_text) <= SKELETON_SEGMENT_MAX_CHARS:
        return [_ChunkSegment(text=source_text)]
    return _split_skeleton_segments(source_text)


def _split_skeleton_segments(source_text: str) -> list[_ChunkSegment]:
    lines = source_text.splitlines()
    if not lines:
        return [_ChunkSegment(text=source_text)]

    segments: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + (1 if current else 0)
        if current and current_len + line_len > SKELETON_SEGMENT_MAX_CHARS:
            segments.append("\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += line_len

    if current:
        segments.append("\n".join(current))

    if len(segments) == 1:
        return [_ChunkSegment(text=segments[0])]

    declaration = _declaration_text(
        chunk_kind="skeleton",
        source_text=source_text,
        signature=None,
    )
    result: list[_ChunkSegment] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        result.append(
            _ChunkSegment(
                text=segment,
                label=f"segment {index}/{total}",
                declaration=declaration,
            )
        )
    return result
