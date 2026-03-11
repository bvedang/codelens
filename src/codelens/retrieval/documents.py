from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class RetrievalDocument:
    chunk_id: str
    kind: str
    name: str | None
    filepath: str | None
    repo_root: str | None
    package_name: str | None
    owner_chain: tuple[str, ...]
    source_set: str | None
    signature: str | None
    return_type: str | None
    field_type: str | None
    component_type: str | None
    annotations: tuple[str, ...]
    modifiers: tuple[str, ...]
    calls: tuple[str, ...]
    fields_accessed: tuple[str, ...]
    throws: tuple[str, ...]
    extends_name: str | None
    implements: tuple[str, ...]
    permits: tuple[str, ...]
    resolved_symbols: tuple[str, ...]
    text: str
    retrieval_text: str


def build_retrieval_documents(
    chunks: Iterable[Mapping],
    *,
    repo_root: str | None = None,
    source_set: str | None = None,
) -> list[RetrievalDocument]:
    chunk_list = list(chunks)
    file_chunk = next((chunk for chunk in chunk_list if chunk.get("kind") == "file"), None)
    documents = []
    for chunk in chunk_list:
        if chunk.get("kind") == "file":
            continue
        documents.append(
            build_retrieval_document(
                chunk,
                file_chunk=file_chunk,
                repo_root=repo_root,
                source_set=source_set,
            )
        )
    return documents


def build_retrieval_document(
    chunk: Mapping,
    *,
    file_chunk: Mapping | None = None,
    repo_root: str | None = None,
    source_set: str | None = None,
) -> RetrievalDocument:
    package_name = _package_name(file_chunk)
    kind = str(chunk["kind"])
    name = chunk.get("name")
    filepath = chunk.get("filepath")
    owner_chain = tuple(chunk.get("owner_chain", []))
    annotations = tuple(_annotation_texts(chunk))
    modifiers = tuple(chunk.get("modifiers", []))
    calls = tuple(chunk.get("calls", []))
    fields_accessed = tuple(chunk.get("fields_accessed", []))
    throws = tuple(chunk.get("throws", []))
    implements = _split_clause(chunk.get("implements"), "implements")
    permits = _split_clause(chunk.get("permits"), "permits")
    signature = _build_signature(chunk)
    resolved_symbols = _resolved_symbols(chunk)
    text = chunk.get("text") or ""

    return RetrievalDocument(
        chunk_id=str(chunk["chunk_id"]),
        kind=kind,
        name=name,
        filepath=filepath,
        repo_root=repo_root,
        package_name=package_name,
        owner_chain=owner_chain,
        source_set=source_set,
        signature=signature,
        return_type=chunk.get("return_type"),
        field_type=chunk.get("field_type"),
        component_type=chunk.get("component_type"),
        annotations=annotations,
        modifiers=modifiers,
        calls=calls,
        fields_accessed=fields_accessed,
        throws=throws,
        extends_name=chunk.get("extends"),
        implements=implements,
        permits=permits,
        resolved_symbols=resolved_symbols,
        text=text,
        retrieval_text=_build_retrieval_text(
            chunk,
            package_name=package_name,
            source_set=source_set,
            signature=signature,
            annotations=annotations,
            modifiers=modifiers,
            calls=calls,
            fields_accessed=fields_accessed,
            throws=throws,
            implements=implements,
            permits=permits,
            resolved_symbols=resolved_symbols,
            text=text,
        ),
    )


def _package_name(file_chunk: Mapping | None) -> str | None:
    if not file_chunk:
        return None
    package_decl = file_chunk.get("package")
    if not package_decl:
        return None
    return str(package_decl).replace("package ", "", 1).rstrip(";").strip()


def _annotation_texts(chunk: Mapping) -> list[str]:
    return [item["text"] for item in chunk.get("annotations", [])]


def _build_signature(chunk: Mapping) -> str | None:
    kind = chunk.get("kind")
    name = chunk.get("name")
    parameters = chunk.get("parameters")
    modifiers = " ".join(chunk.get("modifiers", []))
    prefix = f"{modifiers} " if modifiers else ""

    if kind == "method":
        return_type = chunk.get("return_type")
        if name and parameters and return_type:
            return f"{prefix}{return_type} {name}{parameters}".strip()
    if kind == "constructor" and name and parameters:
        return f"{prefix}{name}{parameters}".strip()
    return None


def _resolved_symbols(chunk: Mapping) -> tuple[str, ...]:
    symbols = []
    field_type = chunk.get("field_type")
    component_type = chunk.get("component_type")
    extends_name = chunk.get("extends")
    if field_type:
        symbols.append(field_type)
    if component_type:
        symbols.append(component_type)
    if extends_name:
        symbols.append(extends_name)
    symbols.extend(chunk.get("calls", []))
    return tuple(dict.fromkeys(symbols))


def _split_clause(value: str | None, keyword: str) -> tuple[str, ...]:
    if not value:
        return ()
    cleaned = value.replace(f"{keyword} ", "", 1).strip()
    return tuple(part.strip() for part in cleaned.split(",") if part.strip())


def _build_retrieval_text(
    chunk: Mapping,
    *,
    package_name: str | None,
    source_set: str | None,
    signature: str | None,
    annotations: tuple[str, ...],
    modifiers: tuple[str, ...],
    calls: tuple[str, ...],
    fields_accessed: tuple[str, ...],
    throws: tuple[str, ...],
    implements: tuple[str, ...],
    permits: tuple[str, ...],
    resolved_symbols: tuple[str, ...],
    text: str,
) -> str:
    parts: list[str] = []
    if package_name:
        parts.append(f"package {package_name}")
    if chunk.get("filepath"):
        parts.append(f"filepath {chunk['filepath']}")
    if source_set:
        parts.append(f"source_set {source_set}")

    owner_chain = chunk.get("owner_chain", [])
    if owner_chain:
        parts.append(f"owner {' '.join(owner_chain)}")

    kind = chunk.get("kind")
    if kind:
        parts.append(f"kind {kind}")
    if chunk.get("name"):
        parts.append(f"name {chunk['name']}")
    if signature:
        parts.append(f"signature {signature}")
    if modifiers:
        parts.append(f"modifiers {' '.join(modifiers)}")
    if annotations:
        parts.append(f"annotations {' '.join(annotations)}")
    if chunk.get("javadoc"):
        parts.append(f"javadoc {chunk['javadoc']}")
    if chunk.get("extends"):
        parts.append(f"extends {chunk['extends']}")
    if implements:
        parts.append(f"implements {' '.join(implements)}")
    if permits:
        parts.append(f"permits {' '.join(permits)}")
    if chunk.get("field_type"):
        parts.append(f"field_type {chunk['field_type']}")
    if chunk.get("component_type"):
        parts.append(f"component_type {chunk['component_type']}")
    if fields_accessed:
        parts.append(f"fields_accessed {' '.join(fields_accessed)}")
    if throws:
        parts.append(f"throws {' '.join(throws)}")
    if calls:
        parts.append(f"calls {' '.join(calls)}")
    if resolved_symbols:
        parts.append(f"resolved_symbols {' '.join(resolved_symbols)}")
    parts.append(f"code {text}")
    return "\n".join(parts)
