"""Tests for chunker.py — parse_java integrations."""

from unittest.mock import Mock

from codelens.chunker import dedup_rerank_results, parse_java
from codelens.parser import JAVA_PARSER
from codelens.type_resolver import (
    ImportContext,
    TypeIndex,
    TypeResolution,
    TypeResolver,
)

_parser = JAVA_PARSER


def _parse(code: bytes):
    return _parser.parse(code)


def _find_node(node, type_name: str):
    """DFS for the first node matching type_name."""
    if node.type == type_name:
        return node
    for child in node.children:
        found = _find_node(child, type_name)
        if found:
            return found
    return None


def _get_chunk(chunks, kind, name):
    for chunk in chunks:
        if chunk["kind"] == kind and chunk.get("name") == name:
            return chunk
    raise AssertionError(f"Missing chunk kind={kind!r} name={name!r}")


def _get_behaviors(chunks):
    return [c for c in chunks if c["kind"] == "behavior"]


# ===========================================================================
# Integration: parse_java — comment attachment
# ===========================================================================


def test_multiline_comments_attach_before_annotated_method():
    code = b"""class X {
// Line one
// Line two
@Override
public void foo() {}
}
"""
    chunks = parse_java(code, filepath="src/X.java")
    method = _get_chunk(chunks, "method", "foo")
    skeleton = _get_chunk(chunks, "skeleton", "X")

    assert method["leading_comment"] == "// Line one\n// Line two"
    assert method["javadoc"] is None
    assert method["parent_chunk_id"] == skeleton["chunk_id"]
    assert method["type_span"] == skeleton["span"]


def test_javadoc_extracted_from_method_chunk():
    code = b"""class X {
/** API docs. */
public void foo() {}
}
"""
    chunks = parse_java(code, filepath="src/X.java")
    method = _get_chunk(chunks, "method", "foo")

    assert method["leading_comment"] == "/** API docs. */"
    assert method["javadoc"] == "/** API docs. */"


# ===========================================================================
# Integration: parse_java — import resolution
# ===========================================================================


def test_imports_resolve_field_calls():
    code = b"""package com.app.orders;

import com.app.payments.PaymentGateway;

class OrderService {
    private final PaymentGateway paymentGateway;

    void placeOrder() {
        paymentGateway.charge();
    }
}
"""
    chunks = parse_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
    )
    method = _get_chunk(chunks, "method", "placeOrder")
    assert "com.app.payments.PaymentGateway.charge" in method["calls"]


def test_imports_resolve_parameter_and_local_variable_calls():
    code = b"""package com.app.orders;

import com.app.payments.PaymentGateway;
import com.app.inventory.StockService;

class OrderService {
    void placeOrder(PaymentGateway gateway) {
        gateway.charge();
        StockService stockService = loadStockService();
        stockService.reserve();
    }
}
"""
    chunks = parse_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
    )
    method = _get_chunk(chunks, "method", "placeOrder")
    assert "com.app.payments.PaymentGateway.charge" in method["calls"]
    assert "com.app.inventory.StockService.reserve" in method["calls"]


def test_same_package_types_resolve_with_project_index():
    code = b"""package com.app.orders;

class OrderService {
    private PaymentGateway paymentGateway;

    void placeOrder() {
        paymentGateway.charge();
    }
}
"""
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["com.app.orders.PaymentGateway"])
    )

    chunks = parse_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
        resolver=resolver,
    )

    method = _get_chunk(chunks, "method", "placeOrder")
    assert "com.app.orders.PaymentGateway.charge" in method["calls"]


def test_wildcard_imports_resolve_with_indexed_types():
    code = b"""package com.app.orders;

import com.app.payments.*;

class OrderService {
    void placeOrder(PaymentGateway gateway) {
        gateway.charge();
    }
}
"""
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["com.app.payments.PaymentGateway"])
    )

    chunks = parse_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
        resolver=resolver,
    )

    method = _get_chunk(chunks, "method", "placeOrder")
    assert "com.app.payments.PaymentGateway.charge" in method["calls"]


def test_java_lang_types_stay_unresolved_without_jdk_index():
    code = b"""class X {
    void normalize(String value) {
        value.trim();
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    method = _get_chunk(chunks, "method", "normalize")
    assert "String.trim" in method["calls"]
    assert "java.lang.String.trim" not in method["calls"]


def test_java_lang_types_resolve_in_calls_with_jdk_index():
    code = b"""class X {
    void normalize(String value) {
        value.trim();
    }
}
"""
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["java.lang.String"])
    )

    chunks = parse_java(code, filepath="X.java", resolver=resolver)
    method = _get_chunk(chunks, "method", "normalize")
    assert "java.lang.String.trim" in method["calls"]


def test_annotation_attributes_resolve_class_literals_without_rewriting_text():
    code = b"""package com.app.orders;

import io.micronaut.context.annotation.Import;
import com.app.generated.ExternalConfiguration;

@Import(classes = ExternalConfiguration.class)
class OrderService {}
"""
    chunks = parse_java(
        code,
        filepath="src/main/java/com/app/orders/OrderService.java",
    )

    type_chunk = _get_chunk(chunks, "type", "OrderService")
    annotation = type_chunk["annotations"][0]
    assert annotation["text"] == "@Import(classes = ExternalConfiguration.class)"
    assert annotation["attributes"]["classes"] == (
        "com.app.generated.ExternalConfiguration.class"
    )


def test_parse_java_accepts_mock_resolver():
    code = b"""class X {
    void send(Gateway gateway) {
        gateway.dispatch();
    }
}
"""
    resolver = Mock()
    resolver.build_import_context.return_value = ImportContext(
        package_name=None,
        explicit_imports={},
        wildcard_imports=(),
    )

    def resolve_type_reference(type_name, import_context, local_types):
        if type_name == "Gateway":
            return TypeResolution(
                source_name=type_name,
                resolved_name="test.mock.Gateway",
                strategy="mock",
            )
        return TypeResolution(
            source_name=type_name, resolved_name=None, strategy="unresolved"
        )

    resolver.resolve_type_reference.side_effect = resolve_type_reference

    chunks = parse_java(code, filepath="X.java", resolver=resolver)

    method = _get_chunk(chunks, "method", "send")
    assert "test.mock.Gateway.dispatch" in method["calls"]
    assert resolver.resolve_type_reference.called


# ===========================================================================
# Integration: parse_java — chunk IDs
# ===========================================================================


def test_chunk_ids_stable_for_same_span_members():
    code = b"""class X {
    private int a, b;
}
"""
    chunks = parse_java(code, filepath="src/X.java")
    field_a = _get_chunk(chunks, "field", "a")
    field_b = _get_chunk(chunks, "field", "b")

    assert field_a["chunk_id"] != field_b["chunk_id"]
    assert field_a["chunk_id"] == "src/X.java:field:X:a:14:31"
    assert field_b["chunk_id"] == "src/X.java:field:X:b:14:31"


# ===========================================================================
# Integration: parse_java — modifiers on chunks
# ===========================================================================


def test_modifiers_on_type_field_method():
    code = b"""public abstract class Service {
    private static final int TIMEOUT = 30;
    protected String name;
    public synchronized void run() {}
}
"""
    chunks = parse_java(code, filepath="Service.java")

    assert _get_chunk(chunks, "type", "Service")["modifiers"] == ["public", "abstract"]
    assert _get_chunk(chunks, "field", "TIMEOUT")["modifiers"] == [
        "private",
        "static",
        "final",
    ]
    assert _get_chunk(chunks, "field", "name")["modifiers"] == ["protected"]
    assert _get_chunk(chunks, "method", "run")["modifiers"] == [
        "public",
        "synchronized",
    ]


def test_constructor_modifiers():
    code = b"""class X {
    private X() {}
    public X(int a) {}
}
"""
    chunks = parse_java(code, filepath="X.java")
    ctors = [c for c in chunks if c["kind"] == "constructor"]
    assert any(c["modifiers"] == ["private"] for c in ctors)
    assert any(c["modifiers"] == ["public"] for c in ctors)


def test_no_modifiers_returns_empty_list():
    code = b"""class X { void run() {} }"""
    chunks = parse_java(code, filepath="X.java")
    assert _get_chunk(chunks, "method", "run")["modifiers"] == []


def test_annotation_excluded_from_chunk_modifiers():
    code = b"""class X {
    @Override
    public void run() {}
}
"""
    chunks = parse_java(code, filepath="X.java")
    method = _get_chunk(chunks, "method", "run")
    assert method["modifiers"] == ["public"]
    assert "@Override" not in method["modifiers"]


# ===========================================================================
# Integration: parse_java — skeleton text
# ===========================================================================


def test_skeleton_extends_not_duplicated():
    code = b"""public class Foo extends Bar {}
"""
    chunks = parse_java(code, filepath="Foo.java")
    skeleton = _get_chunk(chunks, "skeleton", "Foo")
    assert "extends Bar" in skeleton["text"]
    assert "extends extends" not in skeleton["text"]


def test_skeleton_implements_not_duplicated():
    code = b"""public class Foo implements Runnable, Serializable {}
"""
    chunks = parse_java(code, filepath="Foo.java")
    skeleton = _get_chunk(chunks, "skeleton", "Foo")
    assert "implements Runnable, Serializable" in skeleton["text"]
    assert "implements implements" not in skeleton["text"]


def test_skeleton_extends_and_implements_together():
    code = b"""public class Foo extends Bar implements Baz {}
"""
    chunks = parse_java(code, filepath="Foo.java")
    skeleton = _get_chunk(chunks, "skeleton", "Foo")
    assert "extends Bar" in skeleton["text"]
    assert "implements Baz" in skeleton["text"]
    assert "extends extends" not in skeleton["text"]
    assert "implements implements" not in skeleton["text"]


def test_skeleton_includes_type_modifiers():
    code = b"""public final class Config {}
"""
    chunks = parse_java(code, filepath="Config.java")
    skeleton = _get_chunk(chunks, "skeleton", "Config")
    assert "public" in skeleton["text"]
    assert "final" in skeleton["text"]


def test_skeleton_includes_field_modifiers():
    code = b"""class X {
    private static final int MAX = 10;
    protected String label;
}
"""
    chunks = parse_java(code, filepath="X.java")
    skeleton = _get_chunk(chunks, "skeleton", "X")
    assert "private static final int MAX = 10;" in skeleton["text"]
    assert "protected String label;" in skeleton["text"]


def test_skeleton_includes_method_signature_modifiers():
    code = b"""class X {
    public static void main(String[] args) {}
    private int compute() { return 0; }
}
"""
    chunks = parse_java(code, filepath="X.java")
    skeleton = _get_chunk(chunks, "skeleton", "X")
    assert "public" in skeleton["text"]
    assert "static" in skeleton["text"]
    assert "private" in skeleton["text"]


def test_interface_modifiers_on_skeleton():
    chunks = parse_java(
        b"public interface Handler { void handle(); }", filepath="Handler.java"
    )
    skeleton = _get_chunk(chunks, "skeleton", "Handler")
    assert "public" in skeleton["text"]
    assert skeleton["modifiers"] == ["public"]


def test_enum_modifiers_on_skeleton():
    chunks = parse_java(
        b"public enum Status { ACTIVE, INACTIVE; }", filepath="Status.java"
    )
    skeleton = _get_chunk(chunks, "skeleton", "Status")
    assert "public" in skeleton["text"]
    assert skeleton["modifiers"] == ["public"]


# ===========================================================================
# Integration: parse_java — behavior chunk dedup
# ===========================================================================


def test_throw_with_new_emits_single_behavior():
    code = b"""class X {
    void fail() {
        throw new IllegalArgumentException("bad");
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    behaviors = _get_behaviors(chunks)
    assert len(behaviors) == 1
    assert behaviors[0]["node_type"] == "throw_statement"


def test_throw_new_does_not_duplicate_object_creation():
    code = b"""class X {
    void fail() {
        throw new IllegalStateException("msg");
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = [b["node_type"] for b in _get_behaviors(chunks)]
    assert "object_creation_expression" not in node_types


def test_throw_with_anonymous_new_keeps_object_creation_behavior():
    code = b"""class X {
    void fail() {
        throw new RuntimeException() { public String getMessage() { return "x"; } };
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert node_types == {"throw_statement", "object_creation_expression"}

    anon_method = _get_chunk(chunks, "method", "getMessage")
    assert anon_method["owner_chain"] == ["X", "fail", "<anon:RuntimeException>"]


def test_try_with_nested_throw_emits_separate_chunks():
    code = b"""class X {
    void process() {
        try {
            throw new RuntimeException();
        } catch (Exception e) {
            log(e);
        }
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert "try_statement" in node_types
    assert "throw_statement" in node_types


def test_try_with_nested_lambda_emits_separate_chunks():
    code = b"""class X {
    void process() {
        try {
            list.forEach(item -> System.out.println(item));
        } catch (Exception e) {}
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert "try_statement" in node_types
    assert "lambda_expression" in node_types


def test_try_with_nested_method_reference_emits_separate_chunks():
    code = b"""class X {
    void process() {
        try {
            list.forEach(System.out::println);
        } catch (Exception e) {}
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert "try_statement" in node_types
    assert "method_reference" in node_types


def test_nested_behavior_through_intermediates_keeps_all():
    code = b"""class X {
    void process() {
        try {
            throw new RuntimeException("fail");
        } catch (Exception e) {
            Runnable r = () -> log(e);
        }
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert node_types == {"try_statement", "throw_statement", "lambda_expression"}


def test_lambda_with_nested_switch_expression_keeps_both():
    code = b"""class X {
    int build(int x) {
        java.util.function.IntUnaryOperator f = n -> switch (n) { default -> 1; };
        return f.applyAsInt(x);
    }
}
"""
    chunks = parse_java(code, filepath="X.java")
    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert node_types == {"lambda_expression", "switch_expression"}


def test_lambda_with_anonymous_class_keeps_nested_object_creation_behavior():
    code = b"""class X {
    void build() {
        RunnableFactory f = () -> new Runnable() { public void run() {} };
    }
}
"""
    chunks = parse_java(code, filepath="X.java")

    node_types = {b["node_type"] for b in _get_behaviors(chunks)}
    assert node_types == {"lambda_expression", "object_creation_expression"}

    anon_method = _get_chunk(chunks, "method", "run")
    assert anon_method["owner_chain"] == ["X", "build", "<anon:Runnable>"]


# ===========================================================================
# Integration: dedup_rerank_results
# ===========================================================================


def test_dedup_rerank_keeps_first_chunk_per_enclosing_type():
    code = b"""class X {
    void alpha() {}
    void beta() {}
}
"""
    chunks = parse_java(code, filepath="src/X.java")
    method_alpha = _get_chunk(chunks, "method", "alpha")
    method_beta = _get_chunk(chunks, "method", "beta")
    skeleton = _get_chunk(chunks, "skeleton", "X")

    deduped = dedup_rerank_results([method_beta, skeleton, method_alpha])
    assert [chunk["name"] for chunk in deduped] == ["beta"]
