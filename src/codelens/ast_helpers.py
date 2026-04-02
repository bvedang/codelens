import re
from typing import Any

_JAVADOC_RE = re.compile(r"/\*\*.*?\*/", re.DOTALL)
ChunkData = dict[str, Any]

_TYPE_EXPRESSION_KINDS = frozenset(
    {
        "type_identifier",
        "scoped_type_identifier",
        "generic_type",
        "array_type",
        "integral_type",
        "floating_point_type",
        "boolean_type",
        "void_type",
    }
)


def node_text(code: bytes, node) -> str:
    """Extract the source text of a tree-sitter node."""
    return code[node.start_byte : node.end_byte].decode("utf-8")


def first_named(node, *types):
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def node_name(code: bytes, node) -> str | None:
    name = node.child_by_field_name("name")
    if name:
        return node_text(code, name)
    ident = first_named(node, "identifier", "type_identifier")
    return node_text(code, ident) if ident else None


def declarator_names(code: bytes, node) -> list[str]:
    names = []
    if node.type in {
        "field_declaration",
        "constant_declaration",
        "local_variable_declaration",
    }:
        for child in node.named_children:
            if child.type == "variable_declarator":
                name = child.child_by_field_name("name")
                if name:
                    names.append(node_text(code, name))
        return names
    n = node_name(code, node)
    return [n] if n else []


def field_type_str(code: bytes, node) -> str | None:
    """Extract the type string from a field_declaration or constant_declaration."""
    for child in node.named_children:
        if child.type in (
            "type_identifier",
            "scoped_type_identifier",
            "generic_type",
            "array_type",
            "integral_type",
            "floating_point_type",
            "boolean_type",
            "void_type",
        ):
            return node_text(code, child)
    return None


def declared_type_str(code: bytes, node) -> str | None:
    """Extract a declared type from a parameter, local, resource, or field node."""
    type_node = node.child_by_field_name("type")
    if type_node:
        return node_text(code, type_node)
    if node.type == "catch_formal_parameter":
        catch_type = first_named(node, "catch_type")
        if catch_type:
            return node_text(code, catch_type)
    return field_type_str(code, node)


def chunk_id(
    filepath: str | None,
    kind: str,
    owner_chain: list[str],
    name: str | None,
    span: list[int],
) -> str:
    location = filepath or "<memory>"
    owner = ".".join(owner_chain) if owner_chain else "-"
    label = name or "-"
    return f"{location}:{kind}:{owner}:{label}:{span[0]}:{span[1]}"


def location_metadata(node) -> dict[str, int]:
    start_row, start_col = node.start_point
    end_row, end_col = node.end_point
    return {
        "start_line": int(start_row) + 1,
        "end_line": int(end_row) + 1,
        "start_col": int(start_col) + 1,
        "end_col": int(end_col) + 1,
    }


def finalize_chunk(
    chunk: ChunkData, enclosing_type: ChunkData | None = None
) -> ChunkData:
    """Attach deterministic IDs and enclosing-type metadata to a chunk (mutates in place)."""
    owner_chain_value = chunk.get("owner_chain")
    owner_chain = (
        [str(item) for item in owner_chain_value]
        if isinstance(owner_chain_value, (list, tuple))
        else []
    )
    name_value = chunk.get("name")
    name = str(name_value) if name_value is not None else None
    span_value = chunk.get("span")
    span = (
        [int(value) for value in span_value]
        if isinstance(span_value, (list, tuple))
        else [0, 0]
    )
    chunk["chunk_id"] = chunk_id(
        str(chunk.get("filepath")) if chunk.get("filepath") is not None else None,
        str(chunk["kind"]),
        owner_chain,
        name,
        span,
    )
    if enclosing_type:
        parent_chunk_id = enclosing_type.get("chunk_id")
        if parent_chunk_id is not None:
            chunk.setdefault("parent_chunk_id", str(parent_chunk_id))
        enclosing_span = enclosing_type.get("span")
        if isinstance(enclosing_span, (list, tuple)):
            chunk.setdefault("type_span", [int(value) for value in enclosing_span])
    return chunk


def comment_metadata(comment: str | None) -> dict[str, str | None]:
    """Extract leading_comment and javadoc from a comment string."""
    javadoc = None
    if comment:
        match = _JAVADOC_RE.search(comment)
        if match:
            javadoc = match.group(0)
    return {
        "leading_comment": comment,
        "javadoc": javadoc,
    }
