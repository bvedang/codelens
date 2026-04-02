from __future__ import annotations

from collections.abc import Iterable, Mapping

from codelens.models.retrieval_document import RetrievalDocument

ChunkData = Mapping[str, object]


def build_retrieval_documents(
    chunks: Iterable[ChunkData],
    *,
    repo_root: str | None = None,
    source_set: str | None = None,
) -> list[RetrievalDocument]:
    chunk_list = list(chunks)
    file_chunk = next(
        (chunk for chunk in chunk_list if chunk.get("kind") == "file"), None
    )
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
    chunk: ChunkData,
    *,
    file_chunk: ChunkData | None = None,
    repo_root: str | None = None,
    source_set: str | None = None,
) -> RetrievalDocument:
    package_name: str | None = _package_name(file_chunk)
    kind: str = str(chunk["kind"])
    name: str | None = _optional_str(chunk.get("name"))
    filepath: str | None = _optional_str(chunk.get("filepath"))
    owner_chain: list[str] = _string_list(chunk.get("owner_chain"))
    annotations: list[str] = _annotation_texts(chunk)
    modifiers: list[str] = _string_list(chunk.get("modifiers"))
    calls: list[str] = _string_list(chunk.get("calls"))
    fields_accessed: list[str] = _string_list(chunk.get("fields_accessed"))
    throws: list[str] = _string_list(chunk.get("throws"))
    implements: list[str] = _split_clause(
        _optional_str(chunk.get("implements")), "implements"
    )
    permits: list[str] = _split_clause(_optional_str(chunk.get("permits")), "permits")
    signature: str | None = _build_signature(chunk)
    resolved_symbols: list[str] = _resolved_symbols(chunk)
    text: str = _optional_str(chunk.get("text")) or ""
    return_type: str | None = _optional_str(chunk.get("return_type"))
    field_type: str | None = _optional_str(chunk.get("field_type"))
    component_type: str | None = _optional_str(chunk.get("component_type"))
    extends_name: str | None = _optional_str(chunk.get("extends"))

    return RetrievalDocument(
        chunk_id=str(chunk["chunk_id"]),
        kind=kind,
        name=name,
        filepath=filepath,
        repo_root=repo_root,
        package_name=package_name,
        owner_chain=owner_chain,
        source_set=source_set,
        start_line=_optional_int(chunk.get("start_line")),
        end_line=_optional_int(chunk.get("end_line")),
        start_col=_optional_int(chunk.get("start_col")),
        end_col=_optional_int(chunk.get("end_col")),
        signature=signature,
        return_type=return_type,
        field_type=field_type,
        component_type=component_type,
        annotations=annotations,
        modifiers=modifiers,
        calls=calls,
        fields_accessed=fields_accessed,
        throws=throws,
        extends_name=extends_name,
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


def _package_name(file_chunk: ChunkData | None) -> str | None:
    if not file_chunk:
        return None
    package_decl = file_chunk.get("package")
    if not package_decl:
        return None
    return str(package_decl).replace("package ", "", 1).rstrip(";").strip()


def _annotation_texts(chunk: ChunkData) -> list[str]:
    values: list[str] = []
    for item in _object_list(chunk.get("annotations")):
        if isinstance(item, Mapping):
            text = item.get("text")
            if text is not None:
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

    if kind == "method":
        return_type = _optional_str(chunk.get("return_type"))
        if name and parameters and return_type:
            return f"{prefix}{return_type} {name}{parameters}".strip()
    if kind == "constructor" and name and parameters:
        return f"{prefix}{name}{parameters}".strip()
    return None


def _resolved_symbols(chunk: ChunkData) -> list[str]:
    symbols: list[str] = []
    field_type: str | None = _optional_str(chunk.get("field_type"))
    component_type: str | None = _optional_str(chunk.get("component_type"))
    extends_name: str | None = _optional_str(chunk.get("extends"))
    if field_type:
        symbols.append(field_type)
    if component_type:
        symbols.append(component_type)
    if extends_name:
        symbols.append(extends_name)
    symbols.extend(_string_list(chunk.get("calls")))
    return list(dict.fromkeys(symbols))


def _split_clause(value: str | None, keyword: str) -> list[str]:
    if not value:
        return []
    cleaned = value.replace(f"{keyword} ", "", 1).strip()
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return None


def _build_retrieval_text(
    chunk: ChunkData,
    *,
    package_name: str | None,
    source_set: str | None,
    signature: str | None,
    annotations: list[str],
    modifiers: list[str],
    calls: list[str],
    fields_accessed: list[str],
    throws: list[str],
    implements: list[str],
    permits: list[str],
    resolved_symbols: list[str],
    text: str,
) -> str:
    parts: list[str] = []
    if package_name:
        parts.append(f"package {package_name}")
    filepath = _optional_str(chunk.get("filepath"))
    if filepath:
        parts.append(f"filepath {filepath}")
    if source_set:
        parts.append(f"source_set {source_set}")

    owner_chain = _string_list(chunk.get("owner_chain"))
    if owner_chain:
        parts.append(f"owner {' '.join(owner_chain)}")

    kind = _optional_str(chunk.get("kind"))
    if kind:
        parts.append(f"kind {kind}")
    name = _optional_str(chunk.get("name"))
    if name:
        parts.append(f"name {name}")
    if signature:
        parts.append(f"signature {signature}")
    if modifiers:
        parts.append(f"modifiers {' '.join(modifiers)}")
    if annotations:
        parts.append(f"annotations {' '.join(annotations)}")
    javadoc = _optional_str(chunk.get("javadoc"))
    if javadoc:
        parts.append(f"javadoc {javadoc}")
    extends_name = _optional_str(chunk.get("extends"))
    if extends_name:
        parts.append(f"extends {extends_name}")
    if implements:
        parts.append(f"implements {' '.join(implements)}")
    if permits:
        parts.append(f"permits {' '.join(permits)}")
    field_type = _optional_str(chunk.get("field_type"))
    if field_type:
        parts.append(f"field_type {field_type}")
    component_type = _optional_str(chunk.get("component_type"))
    if component_type:
        parts.append(f"component_type {component_type}")
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
