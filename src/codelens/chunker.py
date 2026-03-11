"""
Java code chunker for RAG / Code Lens.

Parses Java source with tree-sitter and emits structured, embeddable chunks
with relationship metadata for the context-engine retrieval pipeline.
"""

from __future__ import annotations

from codelens.ast_helpers import (
    chunk_id,
    comment_metadata,
    declarator_names,
    field_type_str,
    finalize_chunk,
    first_named,
    node_name,
    text,
)
from codelens.constants import (
    BEHAVIOR_NODES,
    MEMBER_NODES,
    SMALL_TYPE_CHAR_LIMIT,
    TYPE_NODES,
)
from codelens.extractors import (
    annotation_texts,
    callable_type_map,
    extract_annotations_full,
    extract_calls,
    extract_fields_accessed,
    extract_modifiers,
    extract_preceding_comment,
    extract_record_components,
    extract_throws,
    _resolve_type_name,
)
from codelens.logging_config import get_logger
from codelens.parser import JAVA_PARSER
from codelens.type_resolver import TypeResolver

parser = JAVA_PARSER
logger = get_logger(__name__)


def method_metadata(code: bytes, node, class_fields: list[str],
                    field_type_map: dict, file_ctx: dict,
                    resolver: TypeResolver) -> dict:
    meta = {}
    ret = node.child_by_field_name("type")
    if ret:
        meta["return_type"] = text(code, ret)

    params = node.child_by_field_name("parameters")
    if params:
        meta["parameters"] = text(code, params)

    meta["annotations"] = extract_annotations_full(code, node, file_ctx, resolver)
    meta["modifiers"] = extract_modifiers(code, node)
    meta["throws"] = extract_throws(code, node)

    body = node.child_by_field_name("body")
    if body is None:
        body = first_named(node, "block", "constructor_body")

    if body:
        meta["calls"] = extract_calls(
            body,
            callable_type_map(code, node, field_type_map, file_ctx, resolver),
            file_ctx,
            resolver,
        )
        meta["fields_accessed"] = extract_fields_accessed(code, body, class_fields)
    else:
        meta["calls"] = []
        meta["fields_accessed"] = []

    return meta


def type_metadata(code: bytes, node, file_ctx: dict,
                  resolver: TypeResolver) -> dict:
    meta = {}
    superclass = node.child_by_field_name("superclass")
    if superclass:
        meta["extends"] = text(code, superclass)

    interfaces = node.child_by_field_name("interfaces")
    if interfaces:
        meta["implements"] = text(code, interfaces)

    permits = node.child_by_field_name("permits")
    if permits:
        meta["permits"] = text(code, permits)

    type_params = node.child_by_field_name("type_parameters")
    if type_params:
        meta["type_parameters"] = text(code, type_params)

    meta["annotations"] = extract_annotations_full(code, node, file_ctx, resolver)
    meta["modifiers"] = extract_modifiers(code, node)

    if superclass:
        st = text(code, superclass)
        meta["is_exception"] = any(
            kw in st for kw in ("Exception", "Error", "Throwable")
        )
    else:
        meta["is_exception"] = False

    return meta


def build_context_header(owner_chain: list[str], file_ctx: dict,
                         class_fields: list[str], filepath: str | None = None) -> str:
    parts = []
    if filepath:
        parts.append(f"// file: {filepath}")
    if file_ctx.get("package"):
        parts.append(f"// {file_ctx['package']}")
    if owner_chain:
        parts.append(f"// class: {'.'.join(owner_chain)}")
    if class_fields:
        parts.append(f"// fields: {', '.join(class_fields)}")
    if parts:
        return "\n".join(parts) + "\n"
    return ""


def build_skeleton(code: bytes, node, owner_chain: list[str], file_ctx: dict,
                   filepath: str | None = None,
                   resolver: TypeResolver | None = None) -> dict:
    name = node_name(code, node)
    body = node.child_by_field_name("body")

    fields = []
    method_sigs = []
    enum_constants = []
    record_components = []

    if node.type == "record_declaration":
        record_components = extract_record_components(code, node)

    if body:
        for child in body.named_children:
            if child.type in {"field_declaration", "constant_declaration"}:
                fields.append(text(code, child).strip())
            elif child.type in {
                "method_declaration", "constructor_declaration",
                "compact_constructor_declaration",
            }:
                sig_parts = []
                for part in child.named_children:
                    if part.type not in ("block", "constructor_body"):
                        sig_parts.append(text(code, part))
                method_sigs.append(" ".join(sig_parts))
            elif child.type == "enum_constant":
                enum_constants.append(
                    node_name(code, child) or text(code, child).strip()
                )

    skeleton_text = _assemble_skeleton_text(
        code, node, name, fields, method_sigs, enum_constants,
        record_components, owner_chain, file_ctx, resolver,
    )

    ctx_header = build_context_header(owner_chain, file_ctx, [], filepath)

    return {
        "kind": "skeleton",
        "name": name,
        "owner_chain": owner_chain[:],
        "filepath": filepath,
        "span": [node.start_byte, node.end_byte],
        "text": skeleton_text,
        "embed_text": ctx_header + skeleton_text,
        "fields": fields,
        "method_signatures": method_sigs,
        "enum_constants": enum_constants,
        "record_components": record_components,
        **type_metadata(code, node, file_ctx, resolver or TypeResolver()),
    }


def _assemble_skeleton_text(code, node, name, fields, method_sigs,
                            enum_constants, record_components, owner_chain,
                            file_ctx=None, resolver=None):
    lines = []
    annotations = extract_annotations_full(code, node, file_ctx, resolver)
    for annotation in annotations:
        lines.append(annotation["text"])

    decl_parts = extract_modifiers(code, node)
    decl_parts.append(node.type.replace("_declaration", "").replace("_", " "))
    if name:
        decl_parts.append(name)

    tp = node.child_by_field_name("type_parameters")
    if tp:
        decl_parts.append(text(code, tp))

    if record_components:
        comp_str = ", ".join(f"{component['type']} {component['name']}" for component in record_components)
        decl_parts.append(f"({comp_str})")

    superclass = node.child_by_field_name("superclass")
    if superclass:
        decl_parts.append(text(code, superclass))

    interfaces = node.child_by_field_name("interfaces")
    if interfaces:
        decl_parts.append(text(code, interfaces))

    permits = node.child_by_field_name("permits")
    if permits:
        decl_parts.append(text(code, permits))

    lines.append(" ".join(decl_parts))

    if owner_chain:
        lines.append(f"  // enclosing: {'.'.join(owner_chain)}")

    if enum_constants:
        lines.append(f"  constants: {', '.join(enum_constants)}")

    for field in fields:
        lines.append(f"  {field}")

    for signature in method_sigs:
        lines.append(f"  {signature}")

    return "\n".join(lines)


def file_context(code: bytes, root) -> dict:
    ctx = {"package": None, "imports": [], "module": None}
    for child in root.named_children:
        if child.type == "package_declaration":
            ctx["package"] = text(code, child)
        elif child.type == "import_declaration":
            ctx["imports"].append(text(code, child))
        elif child.type == "module_declaration":
            ctx["module"] = text(code, child)
    return ctx


def scan_behavior(code: bytes, node, owner_chain: list[str], chunks: list,
                  file_ctx: dict, class_fields: list[str],
                  field_type_map: dict, filepath: str | None,
                  resolver: TypeResolver,
                  enclosing_type: dict | None):

    if node.is_error or node.is_missing:
        chunks.append(finalize_chunk({
            "kind": "parse_error",
            "owner_chain": owner_chain[:],
            "filepath": filepath,
            "span": [node.start_byte, node.end_byte],
            "text": text(code, node),
        }, enclosing_type))

    if node.type == "object_creation_expression":
        anon_body = node.child_by_field_name("body")
        if anon_body is None:
            anon_body = first_named(node, "class_body")
        if anon_body:
            anon_type = node.child_by_field_name("type")
            anon_name = f"<anon:{text(code, anon_type)}>" if anon_type else "<anon>"
            ctx_header = build_context_header(
                owner_chain + [anon_name], file_ctx, class_fields, filepath
            )
            chunks.append(finalize_chunk({
                "kind": "behavior",
                "name": anon_name,
                "owner_chain": owner_chain[:],
                "filepath": filepath,
                "span": [node.start_byte, node.end_byte],
                "text": text(code, node),
                "embed_text": ctx_header + text(code, node),
                "node_type": node.type,
            }, enclosing_type))
            for child in anon_body.named_children:
                walk(code, child, owner_chain + [anon_name], chunks,
                     class_fields, field_type_map, file_ctx, filepath,
                     resolver, enclosing_type)
            return

    if node.type in BEHAVIOR_NODES:
        ctx_header = build_context_header(owner_chain, file_ctx, class_fields, filepath)
        chunks.append(finalize_chunk({
            "kind": "behavior",
            "name": node_name(code, node),
            "owner_chain": owner_chain[:],
            "filepath": filepath,
            "span": [node.start_byte, node.end_byte],
            "text": text(code, node),
            "embed_text": ctx_header + text(code, node),
            "node_type": node.type,
        }, enclosing_type))

    if node.type in TYPE_NODES:
        walk(code, node, owner_chain, chunks, [], {}, file_ctx, filepath,
             resolver, enclosing_type)
        return

    for child in node.named_children:
        if should_skip_behavior_child(node, child):
            continue
        scan_behavior(code, child, owner_chain, chunks, file_ctx,
                      class_fields, field_type_map, filepath, resolver,
                      enclosing_type)


def should_skip_behavior_child(parent, child) -> bool:
    if parent.type != "throw_statement" or child.type != "object_creation_expression":
        return False
    return child.child_by_field_name("body") is None and first_named(child, "class_body") is None


def walk(code: bytes, node, owner_chain: list[str], chunks: list,
         class_fields: list[str], field_type_map: dict,
         file_ctx: dict, filepath: str | None,
         resolver: TypeResolver,
         enclosing_type: dict | None = None):

    if node.is_error or node.is_missing:
        chunks.append(finalize_chunk({
            "kind": "parse_error",
            "owner_chain": owner_chain[:],
            "filepath": filepath,
            "span": [node.start_byte, node.end_byte],
            "text": text(code, node),
        }, enclosing_type))

    if node.type in TYPE_NODES:
        t_meta = type_metadata(code, node, file_ctx, resolver)
        leading_comment = extract_preceding_comment(code, node)
        comment_meta = comment_metadata(leading_comment)
        type_size = node.end_byte - node.start_byte

        if type_size <= SMALL_TYPE_CHAR_LIMIT:
            ctx_header = build_context_header(owner_chain, file_ctx, [], filepath)
            full_text = text(code, node)
            if leading_comment:
                full_text = leading_comment + "\n" + full_text
            chunks.append(finalize_chunk({
                "kind": "type",
                "name": node_name(code, node),
                "owner_chain": owner_chain[:],
                "filepath": filepath,
                "span": [node.start_byte, node.end_byte],
                "text": full_text,
                "embed_text": ctx_header + full_text,
                "type_kind": node.type,
                **comment_meta,
                **t_meta,
            }, enclosing_type))

        skeleton = build_skeleton(code, node, owner_chain, file_ctx, filepath, resolver)
        skeleton.update(comment_meta)
        if leading_comment:
            skeleton["embed_text"] = leading_comment + "\n" + skeleton["embed_text"]
        skeleton = finalize_chunk(skeleton, enclosing_type)
        chunks.append(skeleton)

        body = node.child_by_field_name("body")
        type_name = node_name(code, node)
        next_owner = owner_chain + ([type_name] if type_name else [])
        current_type = {
            "chunk_id": skeleton["chunk_id"],
            "span": skeleton["span"],
        }

        current_fields = []
        current_field_type_map = {}
        if body:
            for child in body.named_children:
                if child.type in {"field_declaration", "constant_declaration"}:
                    ft = field_type_str(code, child)
                    resolved_ft = _resolve_type_name(ft, file_ctx, resolver)
                    for fn in declarator_names(code, child):
                        current_fields.append(fn)
                        if resolved_ft:
                            current_field_type_map[fn] = resolved_ft

        if node.type == "record_declaration":
            for comp in extract_record_components(code, node):
                current_fields.append(comp["name"])
                current_field_type_map[comp["name"]] = (
                    _resolve_type_name(comp["type"], file_ctx, resolver)
                    or comp["type"]
                )
                ctx_header = build_context_header(
                    next_owner, file_ctx, current_fields, filepath
                )
                chunks.append(finalize_chunk({
                    "kind": "record_component",
                    "name": comp["name"],
                    "owner_chain": next_owner[:],
                    "filepath": filepath,
                    "span": [node.start_byte, node.end_byte],
                    "text": f"{comp['type']} {comp['name']}",
                    "embed_text": ctx_header + f"{comp['type']} {comp['name']}",
                    "component_type": comp["type"],
                }, current_type))

        if body:
            for child in body.named_children:
                walk(code, child, next_owner, chunks, current_fields,
                     current_field_type_map, file_ctx, filepath, resolver,
                     current_type)
        return

    if node.type == "static_initializer":
        ctx_header = build_context_header(owner_chain, file_ctx, class_fields, filepath)
        chunks.append(finalize_chunk({
            "kind": "static_initializer",
            "name": "<static_init>",
            "owner_chain": owner_chain[:],
            "filepath": filepath,
            "span": [node.start_byte, node.end_byte],
            "text": text(code, node),
            "embed_text": ctx_header + text(code, node),
            "calls": extract_calls(node, field_type_map, file_ctx, resolver),
        }, enclosing_type))
        return

    if node.type == "block" and node.parent and node.parent.type == "class_body":
        ctx_header = build_context_header(owner_chain, file_ctx, class_fields, filepath)
        chunks.append(finalize_chunk({
            "kind": "instance_initializer",
            "name": "<instance_init>",
            "owner_chain": owner_chain[:],
            "filepath": filepath,
            "span": [node.start_byte, node.end_byte],
            "text": text(code, node),
            "embed_text": ctx_header + text(code, node),
            "calls": extract_calls(node, field_type_map, file_ctx, resolver),
        }, enclosing_type))
        return

    if node.type in MEMBER_NODES:
        kind_map = {
            "method_declaration": "method",
            "constructor_declaration": "constructor",
            "compact_constructor_declaration": "constructor",
            "field_declaration": "field",
            "constant_declaration": "constant",
            "enum_constant": "enum_constant",
            "annotation_type_element_declaration": "annotation_element",
        }

        leading_comment = extract_preceding_comment(code, node)
        comment_meta = comment_metadata(leading_comment)
        ctx_header = build_context_header(owner_chain, file_ctx, class_fields, filepath)

        if node.type in {"field_declaration", "constant_declaration"}:
            for declared_name in declarator_names(code, node):
                raw = text(code, node)
                embed = ctx_header + (leading_comment + "\n" if leading_comment else "") + raw
                chunks.append(finalize_chunk({
                    "kind": kind_map[node.type],
                    "name": declared_name,
                    "owner_chain": owner_chain[:],
                    "filepath": filepath,
                    "span": [node.start_byte, node.end_byte],
                    "text": raw,
                    "embed_text": embed,
                    "node_type": node.type,
                    "field_type": field_type_str(code, node),
                    "annotations": extract_annotations_full(code, node, file_ctx, resolver),
                    "modifiers": extract_modifiers(code, node),
                    **comment_meta,
                }, enclosing_type))

        elif node.type in {
            "method_declaration", "constructor_declaration",
            "compact_constructor_declaration",
        }:
            meta = method_metadata(
                code, node, class_fields, field_type_map, file_ctx, resolver
            )
            raw = text(code, node)
            embed = ctx_header + (leading_comment + "\n" if leading_comment else "") + raw

            chunks.append(finalize_chunk({
                "kind": kind_map[node.type],
                "name": node_name(code, node),
                "owner_chain": owner_chain[:],
                "filepath": filepath,
                "span": [node.start_byte, node.end_byte],
                "text": raw,
                "embed_text": embed,
                "node_type": node.type,
                **comment_meta,
                **meta,
            }, enclosing_type))

        elif node.type == "enum_constant":
            raw = text(code, node)
            embed = ctx_header + (leading_comment + "\n" if leading_comment else "") + raw
            chunks.append(finalize_chunk({
                "kind": "enum_constant",
                "name": node_name(code, node),
                "owner_chain": owner_chain[:],
                "filepath": filepath,
                "span": [node.start_byte, node.end_byte],
                "text": raw,
                "embed_text": embed,
                "node_type": node.type,
                **comment_meta,
            }, enclosing_type))
            body = node.child_by_field_name("body")
            if body is None:
                body = first_named(node, "class_body")
            if body:
                const_name = node_name(code, node) or "<constant>"
                for child in body.named_children:
                    walk(code, child, owner_chain + [const_name], chunks,
                         class_fields, field_type_map, file_ctx, filepath,
                         resolver, enclosing_type)

        else:
            raw = text(code, node)
            embed = ctx_header + (leading_comment + "\n" if leading_comment else "") + raw
            chunks.append(finalize_chunk({
                "kind": kind_map.get(node.type, "member"),
                "name": node_name(code, node),
                "owner_chain": owner_chain[:],
                "filepath": filepath,
                "span": [node.start_byte, node.end_byte],
                "text": raw,
                "embed_text": embed,
                "node_type": node.type,
                **comment_meta,
            }, enclosing_type))

        if node.type in {
            "method_declaration", "constructor_declaration",
            "compact_constructor_declaration",
        }:
            body = node.child_by_field_name("body")
            if body is None:
                body = first_named(node, "block", "constructor_body")
            if body:
                callable_name = node_name(code, node) or "<anonymous>"
                scan_behavior(code, body, owner_chain + [callable_name], chunks,
                              file_ctx, class_fields, field_type_map, filepath,
                              resolver, enclosing_type)
        return

    for child in node.named_children:
        walk(code, child, owner_chain, chunks, class_fields,
             field_type_map, file_ctx, filepath, resolver, enclosing_type)


def parse_java(code: bytes, filepath: str | None = None,
               resolver: TypeResolver | None = None) -> list[dict]:
    """Parse Java source and return all chunks with full metadata."""
    tree = parser.parse(code)
    root = tree.root_node

    resolver = resolver or TypeResolver()
    file_ctx = file_context(code, root)
    file_ctx["import_context"] = resolver.build_import_context(
        file_ctx.get("package"),
        file_ctx.get("imports", []),
    )
    package_name = file_ctx["import_context"].package_name
    file_ctx["declared_types"] = {}
    for child in root.named_children:
        if child.type not in TYPE_NODES:
            continue
        type_name = node_name(code, child)
        if not type_name:
            continue
        file_ctx["declared_types"][type_name] = (
            f"{package_name}.{type_name}" if package_name else type_name
        )

    chunks = [finalize_chunk({
        "kind": "file",
        "filepath": filepath,
        "span": [root.start_byte, root.end_byte],
        "package": file_ctx.get("package"),
        "imports": file_ctx.get("imports", []),
        "module": file_ctx.get("module"),
    })]

    for child in root.named_children:
        if child.type not in {
            "package_declaration", "import_declaration", "module_declaration",
        }:
            walk(code, child, [], chunks, [], {}, file_ctx, filepath, resolver)

    logger.debug("Parsed %s into %d chunks", filepath or "<memory>", len(chunks))
    return chunks


def dedup_rerank_results(chunks: list[dict], limit: int | None = None) -> list[dict]:
    """Keep the first ranked chunk for each enclosing type skeleton."""
    deduped = []
    seen_groups = set()

    for chunk in chunks:
        group_id = chunk.get("parent_chunk_id")
        if group_id is None and chunk.get("kind") in {"type", "skeleton"}:
            group_id = chunk_id(
                chunk.get("filepath"),
                "skeleton",
                chunk.get("owner_chain", []),
                chunk.get("name"),
                chunk["span"],
            )
        if group_id is None:
            group_id = chunk.get("chunk_id")

        if group_id in seen_groups:
            continue

        deduped.append(chunk)
        seen_groups.add(group_id)

        if limit is not None and len(deduped) >= limit:
            break

    return deduped


__all__ = [
    "annotation_texts",
    "dedup_rerank_results",
    "file_context",
    "parse_java",
    "parser",
    "should_skip_behavior_child",
]
