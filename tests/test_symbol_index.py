"""Tests for symbol_index.py."""

from zipfile import ZipFile

from codelens.symbol_index import (
    build_binary_symbol_index,
    build_jar_symbol_index,
    build_jdk_symbol_index,
    build_source_symbol_index,
)


def test_build_source_symbol_index_includes_nested_member_types(tmp_path):
    src = tmp_path / "src" / "main" / "java" / "com" / "app"
    src.mkdir(parents=True)
    java_file = src / "Outer.java"
    java_file.write_text(
        """
package com.app;

class Outer {
    static class Inner {}

    void build() {
        class LocalOnly {}
    }
}
""".strip(),
        encoding="utf-8",
    )

    index = build_source_symbol_index([tmp_path / "src"])

    assert index.qualified_names(origin_kind="source") == (
        "com.app.Outer",
        "com.app.Outer.Inner",
    )


def test_build_source_symbol_index_assigns_source_set_container(tmp_path):
    src = tmp_path / "orders" / "src" / "main" / "java" / "com" / "app" / "orders"
    src.mkdir(parents=True)
    java_file = src / "OrderService.java"
    java_file.write_text(
        "package com.app.orders; class OrderService {}",
        encoding="utf-8",
    )

    index = build_source_symbol_index(
        [tmp_path / "orders"],
        source_set_lookup=lambda path: ":orders:main",
    )

    definitions = index.lookup("OrderService")
    assert len(definitions) == 1
    assert definitions[0].container == ":orders:main"


def test_build_jar_symbol_index_includes_nested_classes_and_skips_anonymous(tmp_path):
    jar_path = tmp_path / "deps.jar"
    with ZipFile(jar_path, "w") as jar:
        jar.writestr("com/app/payments/PaymentGateway.class", b"")
        jar.writestr("com/app/payments/Outer$Inner.class", b"")
        jar.writestr("com/app/payments/Outer$1.class", b"")

    index = build_jar_symbol_index([jar_path])

    assert index.qualified_names(origin_kind="binary") == (
        "com.app.payments.Outer.Inner",
        "com.app.payments.PaymentGateway",
    )


def test_build_binary_symbol_index_reads_class_directories(tmp_path):
    classes_dir = tmp_path / "classes"
    nested_dir = classes_dir / "com" / "app" / "payments"
    nested_dir.mkdir(parents=True)
    (nested_dir / "PaymentGateway.class").write_bytes(b"")
    (nested_dir / "Outer$Inner.class").write_bytes(b"")
    (nested_dir / "Outer$1.class").write_bytes(b"")

    index = build_binary_symbol_index([classes_dir])

    assert index.qualified_names(origin_kind="binary") == (
        "com.app.payments.Outer.Inner",
        "com.app.payments.PaymentGateway",
    )


def test_build_jdk_symbol_index_reads_jmods(tmp_path):
    jmods_dir = tmp_path / "fake-jdk" / "jmods"
    jmods_dir.mkdir(parents=True)
    jmod_path = jmods_dir / "java.base.jmod"
    with ZipFile(jmod_path, "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")
        jmod.writestr("classes/java/lang/RuntimeException.class", b"")
        jmod.writestr("classes/module-info.class", b"")

    index = build_jdk_symbol_index(tmp_path / "fake-jdk")

    assert index.qualified_names(origin_kind="jdk") == (
        "java.lang.RuntimeException",
        "java.lang.String",
    )
