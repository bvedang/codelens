def text(code: bytes, node) -> str:
    return code[node.start_byte : node.end_byte].decode("utf-8")


def first_named(node, *types):
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def node_name(code: bytes, node) -> str | None:
    name = node.child_by_field_name("name")
    if name:
        return text(code, name)
    ident = first_named(node, "identifier", "type_identifier")
    return text(code, ident) if ident else None


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
                    names.append(text(code, name))
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
            return text(code, child)
    return None


def declared_type_str(code: bytes, node) -> str | None:
    """Extract a declared type from a parameter, local, resource, or field node."""
    type_node = node.child_by_field_name("type")
    if type_node:
        return text(code, type_node)
    if node.type == "catch_formal_parameter":
        catch_type = first_named(node, "catch_type")
        if catch_type:
            return text(code, catch_type)
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


def finalize_chunk(chunk: dict, enclosing_type: dict | None = None) -> dict:
    """Attach deterministic IDs and enclosing-type metadata to a chunk."""
    chunk["chunk_id"] = chunk_id(
        chunk.get("filepath"),
        chunk["kind"],
        chunk.get("owner_chain", []),
        chunk.get("name"),
        chunk["span"],
    )
    if enclosing_type:
        chunk.setdefault("parent_chunk_id", enclosing_type["chunk_id"])
        chunk.setdefault("type_span", list(enclosing_type["span"]))
    return chunk


def comment_metadata(comment: str | None) -> dict:
    javadoc = None
    if comment:
        javadoc_lines = []
        in_javadoc = False
        for line in comment.split("\n"):
            if line.lstrip().startswith("/**"):
                in_javadoc = True
            if in_javadoc:
                javadoc_lines.append(line)
            if in_javadoc and "*/" in line:
                in_javadoc = False
        if javadoc_lines:
            javadoc = "\n".join(javadoc_lines)
    return {
        "leading_comment": comment,
        "javadoc": javadoc,
    }
