"""Integration tests for Gradle-scoped resolution."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from codelens.chunker import parse_java
from codelens.gradle_model import GradleWorkspaceModel
from codelens.symbol_index import (
    build_binary_symbol_index,
    build_jdk_symbol_index,
    build_source_symbol_index,
)
from codelens.type_resolver import TypeResolver


def _workspace(tmp_path):
    base = tmp_path.resolve()
    return GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":orders:main": {
                    "source_roots": [str(base / "orders" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [":payments:main"],
                    "external_jars": [str(base / "libs" / "jackson.jar")],
                    "external_binary_entries": [str(base / "libs" / "jackson.jar")],
                },
                ":payments:main": {
                    "source_roots": [str(base / "payments" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
                ":internal:main": {
                    "source_roots": [str(base / "internal" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )


def _get_method(chunks, name):
    for chunk in chunks:
        if chunk["kind"] == "method" and chunk["name"] == name:
            return chunk
    raise AssertionError(f"Missing method chunk {name!r}")


def _resolver_for_file(workspace, file_path, roots, binary_paths=None):
    source_index = build_source_symbol_index(
        roots,
        source_set_lookup=workspace.source_set_lookup,
    )
    binary_index = build_binary_symbol_index(binary_paths) if binary_paths else None
    return TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            file_path,
            source_index=source_index,
            binary_index=binary_index,
        )
    )


def test_gradle_scoped_resolution_uses_visible_project_and_jar_deps(tmp_path):
    orders_src = (
        tmp_path / "orders" / "src" / "main" / "java" / "com" / "app" / "orders"
    )
    payments_src = (
        tmp_path / "payments" / "src" / "main" / "java" / "com" / "app" / "payments"
    )
    internal_src = (
        tmp_path / "internal" / "src" / "main" / "java" / "com" / "app" / "internal"
    )
    libs_dir = tmp_path / "libs"
    orders_src.mkdir(parents=True)
    payments_src.mkdir(parents=True)
    internal_src.mkdir(parents=True)
    libs_dir.mkdir()

    order_service = orders_src / "OrderService.java"
    order_service.write_text(
        """
package com.app.orders;

import com.app.payments.*;
import com.fasterxml.jackson.databind.*;

class OrderService {
    private PaymentGateway paymentGateway;

    void placeOrder(ObjectMapper mapper) {
        paymentGateway.charge();
        mapper.writeValueAsString("ok");
    }
}
""".strip(),
        encoding="utf-8",
    )
    (payments_src / "PaymentGateway.java").write_text(
        "package com.app.payments; public interface PaymentGateway { void charge(); }",
        encoding="utf-8",
    )
    (internal_src / "SecretThing.java").write_text(
        "package com.app.internal; public class SecretThing {}",
        encoding="utf-8",
    )

    with ZipFile(libs_dir / "jackson.jar", "w") as jar:
        jar.writestr("com/fasterxml/jackson/databind/ObjectMapper.class", b"")

    workspace = _workspace(tmp_path)
    source_index = build_source_symbol_index(
        [tmp_path / "orders", tmp_path / "payments", tmp_path / "internal"],
        source_set_lookup=workspace.source_set_lookup,
    )
    jar_index = build_binary_symbol_index([libs_dir / "jackson.jar"])
    resolver = TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            order_service,
            source_index=source_index,
            binary_index=jar_index,
        )
    )

    chunks = parse_java(
        order_service.read_bytes(), filepath=str(order_service), resolver=resolver
    )

    method = _get_method(chunks, "placeOrder")
    assert "com.app.payments.PaymentGateway.charge" in method["calls"]
    assert (
        "com.fasterxml.jackson.databind.ObjectMapper.writeValueAsString"
        in method["calls"]
    )
    assert "com.app.internal.SecretThing" not in " ".join(method["calls"])


def test_gradle_scoped_resolution_does_not_expose_invisible_project_types(tmp_path):
    orders_src = (
        tmp_path / "orders" / "src" / "main" / "java" / "com" / "app" / "orders"
    )
    internal_src = (
        tmp_path / "internal" / "src" / "main" / "java" / "com" / "app" / "internal"
    )
    orders_src.mkdir(parents=True)
    internal_src.mkdir(parents=True)

    order_service = orders_src / "OrderService.java"
    order_service.write_text(
        """
package com.app.orders;

class OrderService {
    void leak(SecretThing secretThing) {
        secretThing.toString();
    }
}
""".strip(),
        encoding="utf-8",
    )
    (internal_src / "SecretThing.java").write_text(
        "package com.app.internal; public class SecretThing {}",
        encoding="utf-8",
    )

    workspace = _workspace(tmp_path)
    source_index = build_source_symbol_index(
        [tmp_path / "orders", tmp_path / "internal"],
        source_set_lookup=workspace.source_set_lookup,
    )
    resolver = TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            order_service,
            source_index=source_index,
            binary_index=None,
        )
    )

    chunks = parse_java(
        order_service.read_bytes(), filepath=str(order_service), resolver=resolver
    )

    method = _get_method(chunks, "leak")
    assert "SecretThing.toString" in method["calls"]


def test_workspace_resolution_supports_same_package_types_without_imports(tmp_path):
    orders_src = (
        tmp_path / "orders" / "src" / "main" / "java" / "com" / "app" / "orders"
    )
    orders_src.mkdir(parents=True)

    service_file = orders_src / "OrderService.java"
    service_file.write_text(
        """
package com.app.orders;

class PaymentGateway {
    void charge() {}
}

class OrderService {
    void placeOrder(PaymentGateway gateway) {
        gateway.charge();
    }
}
""".strip(),
        encoding="utf-8",
    )

    workspace = GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":orders:main": {
                    "source_roots": [
                        str(tmp_path / "orders" / "src" / "main" / "java")
                    ],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )
    resolver = _resolver_for_file(workspace, service_file, [tmp_path / "orders"])

    chunks = parse_java(
        service_file.read_bytes(), filepath=str(service_file), resolver=resolver
    )

    method = _get_method(chunks, "placeOrder")
    assert "com.app.orders.PaymentGateway.charge" in method["calls"]


def test_workspace_resolution_supports_nested_member_types_from_visible_modules(
    tmp_path,
):
    app_src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    shared_src = tmp_path / "shared" / "src" / "main" / "java" / "com" / "shared"
    app_src.mkdir(parents=True)
    shared_src.mkdir(parents=True)

    consumer_file = app_src / "Consumer.java"
    consumer_file.write_text(
        """
package com.app;

import com.shared.Outer.Inner;

class Consumer {
    void use(Inner inner) {
        inner.run();
    }
}
""".strip(),
        encoding="utf-8",
    )
    (shared_src / "Outer.java").write_text(
        """
package com.shared;

public class Outer {
    public static class Inner {
        public void run() {}
    }
}
""".strip(),
        encoding="utf-8",
    )

    workspace = GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":app:main": {
                    "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [":shared:main"],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
                ":shared:main": {
                    "source_roots": [
                        str(tmp_path / "shared" / "src" / "main" / "java")
                    ],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )
    resolver = _resolver_for_file(
        workspace, consumer_file, [tmp_path / "app", tmp_path / "shared"]
    )

    chunks = parse_java(
        consumer_file.read_bytes(), filepath=str(consumer_file), resolver=resolver
    )

    method = _get_method(chunks, "use")
    assert "com.shared.Outer.Inner.run" in method["calls"]


def test_workspace_resolution_supports_test_source_sets_seeing_test_only_project_deps(
    tmp_path,
):
    app_test_src = tmp_path / "app" / "src" / "test" / "java" / "com" / "app"
    helper_test_src = tmp_path / "helper" / "src" / "test" / "java" / "com" / "helper"
    app_test_src.mkdir(parents=True)
    helper_test_src.mkdir(parents=True)

    consumer_file = app_test_src / "OrderServiceTest.java"
    consumer_file.write_text(
        """
package com.app;

import com.helper.TestSupport;

class OrderServiceTest {
    void verify(TestSupport support) {
        support.prepare();
    }
}
""".strip(),
        encoding="utf-8",
    )
    (helper_test_src / "TestSupport.java").write_text(
        """
package com.helper;

public class TestSupport {
    public void prepare() {}
}
""".strip(),
        encoding="utf-8",
    )

    workspace = GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":app:test": {
                    "source_roots": [str(tmp_path / "app" / "src" / "test" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [":helper:test"],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
                ":helper:test": {
                    "source_roots": [
                        str(tmp_path / "helper" / "src" / "test" / "java")
                    ],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )
    resolver = _resolver_for_file(
        workspace, consumer_file, [tmp_path / "app", tmp_path / "helper"]
    )

    chunks = parse_java(
        consumer_file.read_bytes(), filepath=str(consumer_file), resolver=resolver
    )

    method = _get_method(chunks, "verify")
    assert "com.helper.TestSupport.prepare" in method["calls"]


def test_workspace_resolution_indexes_generated_source_roots(tmp_path):
    app_src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    generated_src = (
        tmp_path
        / "helper"
        / "build"
        / "generated"
        / "sources"
        / "annotations"
        / "com"
        / "helper"
    )
    app_src.mkdir(parents=True)
    generated_src.mkdir(parents=True)

    consumer_file = app_src / "Consumer.java"
    consumer_file.write_text(
        """
package com.app;

import com.helper.GeneratedBean;

class Consumer {
    void use(GeneratedBean bean) {
        bean.execute();
    }
}
""".strip(),
        encoding="utf-8",
    )
    (generated_src / "GeneratedBean.java").write_text(
        """
package com.helper;

public class GeneratedBean {
    public void execute() {}
}
""".strip(),
        encoding="utf-8",
    )

    workspace = GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":app:main": {
                    "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [":helper:main"],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
                ":helper:main": {
                    "source_roots": [],
                    "generated_source_roots": [
                        str(
                            tmp_path
                            / "helper"
                            / "build"
                            / "generated"
                            / "sources"
                            / "annotations"
                        )
                    ],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )
    resolver = _resolver_for_file(
        workspace, consumer_file, [tmp_path / "app", tmp_path / "helper"]
    )

    chunks = parse_java(
        consumer_file.read_bytes(), filepath=str(consumer_file), resolver=resolver
    )

    method = _get_method(chunks, "use")
    assert "com.helper.GeneratedBean.execute" in method["calls"]


def test_real_micronaut_local_resolution_with_exported_workspace_model():
    repo = Path(
        "/Users/vedangbarhate/Desktop/workspace/codelens/java_repos/micronaut-core"
    )
    export_path = Path("/tmp/micronaut-workspace-model.json")
    target_files = [
        repo
        / "inject-java"
        / "src"
        / "test"
        / "groovy"
        / "io"
        / "micronaut"
        / "inject"
        / "configuration"
        / "ExternalConfigurationImport.java",
        repo
        / "inject-java"
        / "src"
        / "test"
        / "groovy"
        / "io"
        / "micronaut"
        / "inject"
        / "beans"
        / "external"
        / "ExternalBeanImport.java",
    ]

    if (
        not repo.exists()
        or not export_path.exists()
        or not all(path.exists() for path in target_files)
    ):
        pytest.skip("micronaut-core repo or exported workspace model is not available")

    workspace = GradleWorkspaceModel.from_json_file(export_path)
    for type_name, package_decl, import_decl, target_file, expected in [
        (
            "ExternalConfiguration",
            "package io.micronaut.inject.configuration;",
            "import io.micronaut.inject.test.external.ExternalConfiguration;",
            target_files[0],
            "io.micronaut.inject.test.external.ExternalConfiguration",
        ),
        (
            "ExternalBean",
            "package io.micronaut.inject.beans.external;",
            "import io.micronaut.inject.test.external.ExternalBean;",
            target_files[1],
            "io.micronaut.inject.test.external.ExternalBean",
        ),
    ]:
        source_set_id = workspace.source_set_for_file(target_file)
        assert source_set_id is not None
        visible_source_set_keys = {
            item.key for item in workspace.visible_source_sets(source_set_id)
        }
        expected_source_set = (
            ":micronaut-inject-java-helper:main"
            if type_name == "ExternalConfiguration"
            else ":micronaut-inject-java-helper2:main"
        )
        assert expected_source_set in visible_source_set_keys

        visible_roots = [
            Path(root) for root in workspace.source_sets[expected_source_set].all_roots
        ]
        source_index = build_source_symbol_index(
            visible_roots,
            source_set_lookup=workspace.source_set_lookup,
        )
        resolver = TypeResolver(
            type_index=workspace.visible_type_index_for_file(
                target_file,
                source_index=source_index,
                binary_index=None,
            )
        )

        resolution = resolver.resolve_type_reference(
            type_name,
            resolver.build_import_context(package_decl, [import_decl]),
        )

        assert resolution.best_name() == expected


def test_real_micronaut_external_resolution_with_exported_workspace_model():
    repo = Path(
        "/Users/vedangbarhate/Desktop/workspace/codelens/java_repos/micronaut-core"
    )
    export_path = Path("/tmp/micronaut-workspace-model.json")
    target_file = (
        repo
        / "context"
        / "src"
        / "main"
        / "java"
        / "io"
        / "micronaut"
        / "logging"
        / "PropertiesLoggingLevelsConfigurer.java"
    )

    if not repo.exists() or not export_path.exists() or not target_file.exists():
        pytest.skip("micronaut-core repo or exported workspace model is not available")

    workspace = GradleWorkspaceModel.from_json_file(export_path)
    source_set_id = workspace.source_set_for_file(target_file)
    if source_set_id is None:
        pytest.skip(
            "target file is not mapped to a source set in the exported workspace model"
        )

    binary_paths = [
        Path(entry)
        for entry in workspace.visible_external_binary_entries(source_set_id)
        if "slf4j" in entry.lower() and Path(entry).exists()
    ]
    if not binary_paths:
        pytest.skip(
            "exported workspace model does not include external binary entries for slf4j"
        )

    visible_roots = [
        Path(root) for root in workspace.visible_source_roots(source_set_id)
    ]
    source_index = build_source_symbol_index(
        visible_roots,
        source_set_lookup=workspace.source_set_lookup,
    )
    binary_index = build_binary_symbol_index(binary_paths)
    resolver = TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            target_file,
            source_index=source_index,
            binary_index=binary_index,
        )
    )

    logger_factory_resolution = resolver.resolve_type_reference(
        "LoggerFactory",
        resolver.build_import_context(
            "package io.micronaut.logging;",
            ["import org.slf4j.LoggerFactory;"],
        ),
    )

    assert logger_factory_resolution.best_name() == "org.slf4j.LoggerFactory"
    assert resolver.type_index.lookup("LoggerFactory") == ("org.slf4j.LoggerFactory",)


def test_real_micronaut_jdk_resolution_uses_exported_jdk_home():
    repo = Path(
        "/Users/vedangbarhate/Desktop/workspace/codelens/java_repos/micronaut-core"
    )
    export_path = Path("/tmp/micronaut-workspace-model.json")
    target_file = (
        repo
        / "core"
        / "src"
        / "main"
        / "java"
        / "io"
        / "micronaut"
        / "core"
        / "convert"
        / "format"
        / "ReadableBytesTypeConverter.java"
    )

    if not repo.exists() or not export_path.exists() or not target_file.exists():
        pytest.skip("micronaut-core repo or exported workspace model is not available")

    workspace = GradleWorkspaceModel.from_json_file(export_path)
    if not workspace.jdk_home or not Path(workspace.jdk_home).exists():
        pytest.skip("exported workspace model does not include a usable jdk_home")

    source_set_id = workspace.source_set_for_file(target_file)
    if source_set_id is None:
        pytest.skip(
            "target file is not mapped to a source set in the exported workspace model"
        )

    source_index = build_source_symbol_index(
        [Path(root) for root in workspace.visible_source_roots(source_set_id)],
        source_set_lookup=workspace.source_set_lookup,
    )
    jdk_index = build_jdk_symbol_index(workspace.jdk_home)
    resolver = TypeResolver(
        type_index=workspace.visible_type_index_for_file(
            target_file,
            source_index=source_index,
            jdk_index=jdk_index,
        )
    )

    chunks = parse_java(
        target_file.read_bytes(), filepath=str(target_file), resolver=resolver
    )

    convert_method = _get_method(chunks, "convert")
    parse_size_method = _get_method(chunks, "parseSizeWithUnit")
    string_resolution = resolver.resolve_type_reference(
        "String",
        resolver.build_import_context("package io.micronaut.core.convert.format;", []),
    )
    assert string_resolution.best_name() == "java.lang.String"
    assert "java.lang.String.endsWith" in convert_method["calls"]
    assert "java.lang.String.substring" in parse_size_method["calls"]
    assert "java.lang.String.length" in parse_size_method["calls"]


def test_workspace_resolution_uses_external_class_directories_and_excludes_project_jars(
    tmp_path,
):
    app_src = tmp_path / "app" / "src" / "main" / "java" / "com" / "app"
    shared_src = tmp_path / "shared" / "src" / "main" / "java" / "com" / "shared"
    external_classes = tmp_path / "external" / "classes" / "org" / "slf4j"
    local_jar = tmp_path / "shared" / "build" / "libs" / "shared.jar"
    app_src.mkdir(parents=True)
    shared_src.mkdir(parents=True)
    external_classes.mkdir(parents=True)
    local_jar.parent.mkdir(parents=True)

    consumer_file = app_src / "Consumer.java"
    consumer_file.write_text(
        """
package com.app;

import org.slf4j.LoggerFactory;

class Consumer {
    void log() {
        LoggerFactory.getLogger(Consumer.class);
    }
}
""".strip(),
        encoding="utf-8",
    )
    (shared_src / "ProjectOnly.java").write_text(
        "package com.shared; public class ProjectOnly {}",
        encoding="utf-8",
    )
    (external_classes / "LoggerFactory.class").write_bytes(b"")
    with ZipFile(local_jar, "w") as jar:
        jar.writestr("com/shared/ProjectOnly.class", b"")

    workspace = GradleWorkspaceModel.from_dict(
        {
            "source_sets": {
                ":app:main": {
                    "source_roots": [str(tmp_path / "app" / "src" / "main" / "java")],
                    "generated_source_roots": [],
                    "project_dependencies": [":shared:main"],
                    "external_jars": [],
                    "project_artifact_entries": [str(local_jar)],
                    "external_binary_entries": [str(tmp_path / "external" / "classes")],
                    "compile_classpath_entries": [
                        str(local_jar),
                        str(tmp_path / "external" / "classes"),
                    ],
                },
                ":shared:main": {
                    "source_roots": [
                        str(tmp_path / "shared" / "src" / "main" / "java")
                    ],
                    "generated_source_roots": [],
                    "project_dependencies": [],
                    "external_jars": [],
                    "external_binary_entries": [],
                },
            }
        }
    )
    resolver = _resolver_for_file(
        workspace,
        consumer_file,
        [tmp_path / "app", tmp_path / "shared"],
        binary_paths=[local_jar, tmp_path / "external" / "classes"],
    )

    chunks = parse_java(
        consumer_file.read_bytes(), filepath=str(consumer_file), resolver=resolver
    )

    method = _get_method(chunks, "log")
    assert "org.slf4j.LoggerFactory.getLogger" in method["calls"]
    assert resolver.type_index.lookup("ProjectOnly") == ("com.shared.ProjectOnly",)
