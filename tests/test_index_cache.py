"""Tests for index_cache.py."""

from zipfile import ZipFile

from codelens.index_cache import IndexCache


def test_source_index_cache_reuses_unchanged_inputs(tmp_path):
    src = tmp_path / "src" / "main" / "java" / "com" / "app"
    src.mkdir(parents=True)
    (src / "OrderService.java").write_text(
        "package com.app; class OrderService {}",
        encoding="utf-8",
    )

    cache = IndexCache()
    first = cache.get_source_index([tmp_path / "src"], context_token="workspace-v1")
    second = cache.get_source_index([tmp_path / "src"], context_token="workspace-v1")

    assert first is second


def test_source_index_cache_invalidates_when_source_changes(tmp_path):
    src = tmp_path / "src" / "main" / "java" / "com" / "app"
    src.mkdir(parents=True)
    java_file = src / "OrderService.java"
    java_file.write_text(
        "package com.app; class OrderService {}",
        encoding="utf-8",
    )

    cache = IndexCache()
    first = cache.get_source_index([tmp_path / "src"], context_token="workspace-v1")

    java_file.write_text(
        "package com.app; class OrderService {} class PaymentGateway {}",
        encoding="utf-8",
    )

    second = cache.get_source_index([tmp_path / "src"], context_token="workspace-v1")

    assert first is not second
    assert second.lookup("PaymentGateway")


def test_binary_index_cache_invalidates_when_jar_changes(tmp_path):
    jar_path = tmp_path / "deps.jar"
    with ZipFile(jar_path, "w") as jar:
        jar.writestr("com/app/payments/PaymentGateway.class", b"")

    cache = IndexCache()
    first = cache.get_binary_index([jar_path])

    with ZipFile(jar_path, "w") as jar:
        jar.writestr("com/app/payments/PaymentGateway.class", b"")
        jar.writestr("org/slf4j/LoggerFactory.class", b"")

    second = cache.get_binary_index([jar_path])

    assert first is not second
    assert second.lookup("LoggerFactory")


def test_jdk_index_cache_invalidates_when_jmod_changes(tmp_path):
    jmods_dir = tmp_path / "fake-jdk" / "jmods"
    jmods_dir.mkdir(parents=True)
    jmod_path = jmods_dir / "java.base.jmod"
    with ZipFile(jmod_path, "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")

    cache = IndexCache()
    first = cache.get_jdk_index(tmp_path / "fake-jdk")

    with ZipFile(jmod_path, "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")
        jmod.writestr("classes/java/lang/RuntimeException.class", b"")

    second = cache.get_jdk_index(tmp_path / "fake-jdk")

    assert first is not second
    assert second.lookup("RuntimeException")
