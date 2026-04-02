from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from codelens.ast_helpers import (
    declarator_names,
    declared_type_str,
    first_named,
    node_name,
    node_text,
)
from codelens.constants import COMMENT_TYPES, TYPE_NODES
from codelens.type_resolver import TypeResolver

_CLASS_LITERAL_RE = re.compile(r"^([A-Za-z_][$\w]*(?:\.[A-Za-z_][$\w]*)*)\.class$")
FileContext = Mapping[str, Any]
AnnotationData = dict[str, Any]
FieldTypeMap = Mapping[str, str]
SymbolTypeMap = dict[str, str]
RecordComponent = dict[str, str]


def extract_preceding_comment(code: bytes, node) -> str | None:
    """Grab the contiguous comment block immediately preceding a node."""
    prev = node.prev_sibling
    current_start = node.start_byte

    comments = []
    while prev:
        gap = code[prev.end_byte : current_start].decode("utf-8")
        if gap.strip():
            break
        if "\n\n" in gap.replace("\r\n", "\n"):
            break
        if prev.type not in COMMENT_TYPES:
            break
        comments.append(node_text(code, prev))
        current_start = prev.start_byte
        prev = prev.prev_sibling

    if not comments:
        return None

    comments.reverse()
    return "\n".join(comments)


def _resolve_type_name(
    type_name: str | None, file_ctx: dict, resolver: TypeResolver
) -> str | None:
    resolution = resolver.resolve_type_reference(
        type_name,
        file_ctx.get("import_context"),
        file_ctx.get("declared_types"),
    )
    return resolution.best_name()


def _resolve_annotation_attribute_value(
    value: str, file_ctx: dict | None, resolver: TypeResolver | None
) -> str:
    if not file_ctx or not resolver:
        return value

    match = _CLASS_LITERAL_RE.match(value)
    if not match:
        return value

    resolved_name = _resolve_type_name(match.group(1), file_ctx, resolver)
    if not resolved_name or resolved_name == match.group(1):
        return value
    return f"{resolved_name}.class"


def extract_annotations_full(
    code: bytes,
    node,
    file_ctx: dict | None = None,
    resolver: TypeResolver | None = None,
) -> list[dict]:
    """Get annotations with their attribute values parsed out."""
    annotations: list[AnnotationData] = []
    modifiers = first_named(node, "modifiers")
    source = modifiers if modifiers else node
    for child in source.named_children:
        if child.type == "marker_annotation":
            annotations.append(
                {
                    "text": node_text(code, child),
                    "name": node_name(code, child),
                    "attributes": {},
                }
            )
        elif child.type == "annotation":
            attrs: dict[str, str] = {}
            args = child.child_by_field_name("arguments")
            if args:
                for arg in args.named_children:
                    if arg.type == "element_value_pair":
                        key_node = arg.child_by_field_name("key")
                        val_node = arg.child_by_field_name("value")
                        if key_node and val_node:
                            attrs[node_text(code, key_node)] = (
                                _resolve_annotation_attribute_value(
                                    node_text(code, val_node),
                                    file_ctx,
                                    resolver,
                                )
                            )
                    else:
                        attrs["value"] = _resolve_annotation_attribute_value(
                            node_text(code, arg),
                            file_ctx,
                            resolver,
                        )
            annotations.append(
                {
                    "text": node_text(code, child),
                    "name": node_name(code, child),
                    "attributes": attrs,
                }
            )
    return annotations


def annotation_texts(annots: list[AnnotationData]) -> list[str]:
    return [str(a["text"]) for a in annots]


def extract_modifiers(code: bytes, node) -> list[str]:
    mods_node = first_named(node, "modifiers")
    if mods_node is None:
        return []
    return [
        node_text(code, child) for child in mods_node.children if not child.is_named
    ]


def flatten_method_chain(node) -> list[str]:
    """Flatten chained calls into individual method targets."""
    calls = []
    if node.type == "method_invocation":
        obj = node.child_by_field_name("object")
        name = node.child_by_field_name("name")

        if obj and obj.type == "method_invocation":
            calls.extend(flatten_method_chain(obj))
            if name:
                calls.append(name.text.decode())
        elif obj and name:
            calls.append(
                f"{obj.text.decode()}.{name.text.decode()}"
                if obj.type in {"identifier", "this"}
                else f"<expr>.{name.text.decode()}"
            )
        elif name:
            calls.append(name.text.decode())

    return calls


def _resolve_call_target(
    call: str,
    field_type_map: dict | None,
    file_ctx: dict | None,
    resolver: TypeResolver | None,
) -> str:
    if "." not in call:
        return call

    head, tail = call.split(".", 1)
    if field_type_map and head in field_type_map:
        return f"{field_type_map[head]}.{tail}"

    if resolver and file_ctx:
        resolved_head = _resolve_type_name(head, file_ctx, resolver)
        if resolved_head and resolved_head != head:
            return f"{resolved_head}.{tail}"

    return call


def extract_calls(
    node,
    field_type_map: dict | None = None,
    file_ctx: dict | None = None,
    resolver: TypeResolver | None = None,
) -> list[str]:
    """Walk a subtree and collect every invocation or method reference."""
    calls = []

    if node.type == "method_invocation":
        raw = flatten_method_chain(node)
        for call in raw:
            calls.append(_resolve_call_target(call, field_type_map, file_ctx, resolver))

        args = node.child_by_field_name("arguments")
        if args:
            for child in args.named_children:
                calls.extend(extract_calls(child, field_type_map, file_ctx, resolver))
        return calls

    if node.type == "method_reference":
        calls.append(node.text.decode())

    if node.type == "explicit_constructor_invocation":
        calls.append("super()")
    if node.type == "super":
        parent = node.parent
        if parent and parent.type == "method_invocation":
            name = parent.child_by_field_name("name")
            if name:
                calls.append(f"super.{name.text.decode()}")

    for child in node.named_children:
        calls.extend(extract_calls(child, field_type_map, file_ctx, resolver))
    return calls


def _collect_declared_symbols(
    code: bytes,
    node,
    file_ctx: dict,
    resolver: TypeResolver,
    symbol_type_map: dict[str, str],
) -> None:
    if node.type in TYPE_NODES:
        return

    declared_type = None
    declared_names = []

    if node.type in {
        "formal_parameter",
        "spread_parameter",
        "local_variable_declaration",
        "resource",
        "catch_formal_parameter",
    }:
        declared_type = declared_type_str(code, node)
        declared_names = declarator_names(code, node)
    elif node.type == "enhanced_for_statement":
        declared_type = declared_type_str(code, node)
        name = node.child_by_field_name("name")
        if name:
            declared_names = [node_text(code, name)]

    if declared_type:
        resolved_type = (
            _resolve_type_name(declared_type, file_ctx, resolver) or declared_type
        )
        for declared_name in declared_names:
            symbol_type_map[declared_name] = resolved_type

    for child in node.named_children:
        _collect_declared_symbols(code, child, file_ctx, resolver, symbol_type_map)


def callable_type_map(
    code: bytes, node, field_type_map: dict, file_ctx: dict, resolver: TypeResolver
) -> dict[str, str]:
    """Merge fields with callable-scoped declarations for call resolution."""
    merged = dict(field_type_map)

    parameters = node.child_by_field_name("parameters")
    if parameters:
        _collect_declared_symbols(code, parameters, file_ctx, resolver, merged)

    body = node.child_by_field_name("body")
    if body is None:
        body = first_named(node, "block", "constructor_body")
    if body:
        _collect_declared_symbols(code, body, file_ctx, resolver, merged)

    return merged


def extract_fields_accessed(code: bytes, node, class_fields: list[str]) -> list[str]:
    accessed = set()
    _walk_field_access(node, class_fields, accessed)
    return sorted(accessed)


def _walk_field_access(node, class_fields: list[str], accessed: set[str]) -> None:
    if node.type == "field_access":
        obj = node.child_by_field_name("object")
        field = node.child_by_field_name("field")
        if (
            obj
            and field
            and obj.text.decode() == "this"
            and field.text.decode() in class_fields
        ):
            accessed.add(field.text.decode())

    if node.type == "identifier":
        name = node.text.decode()
        if (
            name in class_fields
            and node.parent
            and node.parent.type
            not in {
                "formal_parameter",
                "local_variable_declaration",
                "catch_formal_parameter",
                "enhanced_for_statement",
                "lambda_expression",
            }
        ):
            accessed.add(name)

    for child in node.named_children:
        _walk_field_access(child, class_fields, accessed)


def extract_throws(code: bytes, node) -> list[str]:
    throws = node.child_by_field_name("throws")
    if throws is None:
        for child in node.named_children:
            if child.type == "throws":
                throws = child
                break
    if throws is None:
        return []
    return [
        node_text(code, child)
        for child in throws.named_children
        if child.type in ("type_identifier", "scoped_type_identifier", "generic_type")
    ]


def extract_record_components(code: bytes, node) -> list[RecordComponent]:
    """Extract components from record Foo(String bar, int baz)."""
    components: list[RecordComponent] = []
    params = node.child_by_field_name("parameters")
    if params is None:
        params = first_named(node, "formal_parameters")
    if params is None:
        return components
    for child in params.named_children:
        if child.type == "formal_parameter":
            ptype = child.child_by_field_name("type")
            pname = child.child_by_field_name("name")
            if ptype and pname:
                components.append(
                    {
                        "name": node_text(code, pname),
                        "type": node_text(code, ptype),
                    }
                )
    return components
