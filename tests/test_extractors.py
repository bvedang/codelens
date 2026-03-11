from codelens.chunker import should_skip_behavior_child
from codelens.extractors import extract_modifiers
from codelens.parser import JAVA_PARSER

_parser = JAVA_PARSER


def _parse(code: bytes):
    return _parser.parse(code)


def _find_node(node, type_name: str):
    if node.type == type_name:
        return node
    for child in node.children:
        found = _find_node(child, type_name)
        if found:
            return found
    return None


def test_extract_modifiers_public_class():
    code = b"public class Foo {}"
    node = _find_node(_parse(code).root_node, "class_declaration")
    assert extract_modifiers(code, node) == ["public"]


def test_extract_modifiers_private_static_final_field():
    code = b"class X { private static final int MAX = 1; }"
    node = _find_node(_parse(code).root_node, "field_declaration")
    assert extract_modifiers(code, node) == ["private", "static", "final"]


def test_extract_modifiers_protected_method():
    code = b"class X { protected void run() {} }"
    node = _find_node(_parse(code).root_node, "method_declaration")
    assert extract_modifiers(code, node) == ["protected"]


def test_extract_modifiers_no_modifiers():
    code = b"class X { void run() {} }"
    node = _find_node(_parse(code).root_node, "method_declaration")
    assert extract_modifiers(code, node) == []


def test_extract_modifiers_excludes_annotations():
    code = b"class X { @Override public void run() {} }"
    node = _find_node(_parse(code).root_node, "method_declaration")
    mods = extract_modifiers(code, node)
    assert mods == ["public"]
    assert "@Override" not in mods


def test_extract_modifiers_multiple_annotations_only_keywords():
    code = b'class X { @SuppressWarnings("all") @Deprecated public static void old() {} }'
    node = _find_node(_parse(code).root_node, "method_declaration")
    assert extract_modifiers(code, node) == ["public", "static"]


def test_extract_modifiers_annotation_only_field():
    code = b"class X { @Nullable String name; }"
    node = _find_node(_parse(code).root_node, "field_declaration")
    assert extract_modifiers(code, node) == []


def test_extract_modifiers_abstract_method():
    code = b"abstract class X { protected abstract void process(); }"
    node = _find_node(_parse(code).root_node, "method_declaration")
    assert extract_modifiers(code, node) == ["protected", "abstract"]


def test_extract_modifiers_synchronized():
    code = b"class X { public synchronized void lock() {} }"
    node = _find_node(_parse(code).root_node, "method_declaration")
    assert extract_modifiers(code, node) == ["public", "synchronized"]


def test_extract_modifiers_final_class():
    code = b"public final class Constants {}"
    node = _find_node(_parse(code).root_node, "class_declaration")
    assert extract_modifiers(code, node) == ["public", "final"]


def test_extract_modifiers_private_constructor():
    code = b"class X { private X() {} }"
    node = _find_node(_parse(code).root_node, "constructor_declaration")
    assert extract_modifiers(code, node) == ["private"]


def test_extract_modifiers_public_interface():
    code = b"public interface Handler { void handle(); }"
    node = _find_node(_parse(code).root_node, "interface_declaration")
    assert extract_modifiers(code, node) == ["public"]


def test_extract_modifiers_public_enum():
    code = b"public enum Status { ACTIVE, INACTIVE }"
    node = _find_node(_parse(code).root_node, "enum_declaration")
    assert extract_modifiers(code, node) == ["public"]


def test_should_skip_behavior_child_for_throw_new_without_anonymous_body():
    code = b"class X { void fail() { throw new IllegalStateException(); } }"
    parent = _find_node(_parse(code).root_node, "throw_statement")
    child = _find_node(parent, "object_creation_expression")
    assert should_skip_behavior_child(parent, child) is True


def test_should_skip_behavior_child_keeps_throw_new_with_anonymous_body():
    code = (
        b'class X { void fail() { throw new RuntimeException() '
        b'{ public String getMessage() { return "x"; } }; } }'
    )
    parent = _find_node(_parse(code).root_node, "throw_statement")
    child = _find_node(parent, "object_creation_expression")
    assert should_skip_behavior_child(parent, child) is False


def test_should_skip_behavior_child_keeps_other_behavior_pairs():
    code = b"class X { void build() { RunnableFactory f = () -> new Runnable() { public void run() {} }; } }"
    parent = _find_node(_parse(code).root_node, "lambda_expression")
    child = _find_node(parent, "object_creation_expression")
    assert should_skip_behavior_child(parent, child) is False
