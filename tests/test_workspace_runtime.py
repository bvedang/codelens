"""Tests for workspace_runtime.py."""

import json
from zipfile import ZipFile

from codelens.chunker import parse_java
from codelens.index_cache import IndexCache
from codelens.workspace_runtime import build_workspace_resolver_context, parse_java_file_with_workspace


def test_build_workspace_resolver_context_assembles_source_binary_and_jdk_indexes(tmp_path):
    app_src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    shared_src = tmp_path / "shared" / "src" / "main" / "java" / "com" / "shared"
    libs_dir = tmp_path / "libs"
    jmods_dir = tmp_path / "fake-jdk" / "jmods"
    app_src.mkdir(parents=True)
    shared_src.mkdir(parents=True)
    libs_dir.mkdir()
    jmods_dir.mkdir(parents=True)

    consumer_file = app_src / "Consumer.java"
    consumer_file.write_text(
        """
package com.app;

import com.shared.SharedGateway;
import org.slf4j.LoggerFactory;

class Consumer {
    void use(SharedGateway gateway, String value) {
        gateway.charge();
        LoggerFactory.getLogger(Consumer.class);
        value.trim();
    }
}
""".strip(),
        encoding="utf-8",
    )
    (shared_src / "SharedGateway.java").write_text(
        "package com.shared; public interface SharedGateway { void charge(); }",
        encoding="utf-8",
    )
    with ZipFile(libs_dir / "slf4j-api.jar", "w") as jar:
        jar.writestr("org/slf4j/LoggerFactory.class", b"")
    with ZipFile(jmods_dir / "java.base.jmod", "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")

    workspace_json = tmp_path / "workspace.json"
    workspace_json.write_text(json.dumps({
        "schema_version": 1,
        "jdk_home": str(tmp_path / "fake-jdk"),
        "source_sets": {
            ":app:main": {
                "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [":shared:main"],
                "external_jars": [str(libs_dir / "slf4j-api.jar")],
                "external_binary_entries": [str(libs_dir / "slf4j-api.jar")],
            },
            ":shared:main": {
                "source_roots": [str(tmp_path / "shared" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": [],
                "external_binary_entries": [],
            },
        },
    }), encoding="utf-8")

    context = build_workspace_resolver_context(
        consumer_file,
        workspace_json=workspace_json,
        resolve_binaries=True,
    )

    assert context.source_set_id is not None
    assert context.source_set_id.key == ":app:main"
    assert context.binary_index is not None
    assert context.jdk_index is not None

    chunks = parse_java(consumer_file.read_bytes(), filepath=str(consumer_file), resolver=context.resolver)
    method = next(chunk for chunk in chunks if chunk["kind"] == "method" and chunk["name"] == "use")
    assert "com.shared.SharedGateway.charge" in method["calls"]
    assert "org.slf4j.LoggerFactory.getLogger" in method["calls"]
    assert "java.lang.String.trim" in method["calls"]


def test_parse_java_file_with_workspace_returns_chunks_and_context(tmp_path):
    src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    src.mkdir(parents=True)
    java_file = src / "Hello.java"
    java_file.write_text(
        "package com.app; class Hello { String value() { String text = \"x\"; return text.trim(); } }",
        encoding="utf-8",
    )

    jmods_dir = tmp_path / "fake-jdk" / "jmods"
    jmods_dir.mkdir(parents=True)
    with ZipFile(jmods_dir / "java.base.jmod", "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")

    workspace_json = tmp_path / "workspace.json"
    workspace_json.write_text(json.dumps({
        "schema_version": 1,
        "jdk_home": str(tmp_path / "fake-jdk"),
        "source_sets": {
            ":app:main": {
                "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": [],
                "external_binary_entries": [],
            },
        },
    }), encoding="utf-8")

    chunks, context = parse_java_file_with_workspace(java_file, workspace_json=workspace_json)

    assert context.filepath == str(java_file.resolve())
    method = next(chunk for chunk in chunks if chunk["kind"] == "method" and chunk["name"] == "value")
    assert "java.lang.String.trim" in method["calls"]


def test_workspace_runtime_reuses_cached_indexes_until_inputs_change(tmp_path):
    src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    src.mkdir(parents=True)
    java_file = src / "Hello.java"
    java_file.write_text(
        "package com.app; class Hello { String value() { String text = \"x\"; return text.trim(); } }",
        encoding="utf-8",
    )

    jmods_dir = tmp_path / "fake-jdk" / "jmods"
    jmods_dir.mkdir(parents=True)
    with ZipFile(jmods_dir / "java.base.jmod", "w") as jmod:
        jmod.writestr("classes/java/lang/String.class", b"")

    workspace_json = tmp_path / "workspace.json"
    workspace_json.write_text(json.dumps({
        "schema_version": 1,
        "jdk_home": str(tmp_path / "fake-jdk"),
        "source_sets": {
            ":app:main": {
                "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": [],
                "external_binary_entries": [],
            },
        },
    }), encoding="utf-8")

    cache = IndexCache()
    first = build_workspace_resolver_context(
        java_file,
        workspace_json=workspace_json,
        index_cache=cache,
    )
    second = build_workspace_resolver_context(
        java_file,
        workspace_json=workspace_json,
        index_cache=cache,
    )

    assert first.source_index is second.source_index
    assert first.jdk_index is second.jdk_index

    java_file.write_text(
        "package com.app; class Hello { String value() { String text = \"x\"; return text.strip(); } }",
        encoding="utf-8",
    )

    third = build_workspace_resolver_context(
        java_file,
        workspace_json=workspace_json,
        index_cache=cache,
    )

    assert third.source_index is not first.source_index
    assert third.jdk_index is first.jdk_index
