"""Tests for gradle_model.py."""

from codelens.gradle_model import GradleWorkspaceModel, SourceSetId
from codelens.symbol_index import SymbolDefinition, SymbolIndex


def _workspace_dict(tmp_path):
    base = tmp_path.resolve()
    return {
        "jdk_home": str(base / "fake-jdk"),
        "source_sets": {
            ":orders:main": {
                "source_roots": [str(base / "orders" / "src" / "main" / "java")],
                "generated_source_roots": [str(base / "orders" / "build" / "generated")],
                "project_dependencies": [":payments:main"],
                "external_jars": [str(base / "libs" / "jackson.jar")],
                "project_artifact_entries": [str(base / "payments" / "build" / "libs" / "payments.jar")],
                "external_binary_entries": [str(base / "libs" / "jackson.jar")],
                "output_dirs": [str(base / "orders" / "build" / "classes" / "java" / "main")],
                "compile_classpath_entries": [
                    str(base / "payments" / "build" / "classes" / "java" / "main"),
                    str(base / "payments" / "build" / "libs" / "payments.jar"),
                    str(base / "libs" / "jackson.jar"),
                ],
            },
            ":payments:main": {
                "source_roots": [str(base / "payments" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": [str(base / "libs" / "slf4j.jar")],
                "external_binary_entries": [
                    str(base / "libs" / "slf4j.jar"),
                    str(base / "deps" / "classes"),
                ],
                "output_dirs": [str(base / "payments" / "build" / "classes" / "java" / "main")],
                "compile_classpath_entries": [
                    str(base / "libs" / "slf4j.jar"),
                    str(base / "deps" / "classes"),
                ],
            },
            ":internal:main": {
                "source_roots": [str(base / "internal" / "src" / "main" / "java")],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": [],
                "output_dirs": [str(base / "internal" / "build" / "classes" / "java" / "main")],
            },
        }
    }


def test_source_set_for_file_prefers_longest_matching_root(tmp_path):
    workspace = GradleWorkspaceModel.from_dict(_workspace_dict(tmp_path))
    generated_file = (
        tmp_path / "orders" / "build" / "generated" / "com" / "app" / "orders" / "Generated.java"
    )

    source_set_id = workspace.source_set_for_file(generated_file)

    assert source_set_id == SourceSetId(project_path=":orders", name="main")


def test_workspace_model_preserves_jdk_home(tmp_path):
    workspace = GradleWorkspaceModel.from_dict(_workspace_dict(tmp_path))

    assert workspace.jdk_home == str(tmp_path.resolve() / "fake-jdk")


def test_visible_source_sets_include_transitive_project_dependencies(tmp_path):
    workspace_dict = _workspace_dict(tmp_path)
    workspace_dict["source_sets"][":payments:main"]["project_dependencies"] = [":internal:main"]
    workspace = GradleWorkspaceModel.from_dict(workspace_dict)

    visible = workspace.visible_source_sets(SourceSetId(project_path=":orders", name="main"))

    assert [item.key for item in visible] == [
        ":orders:main",
        ":payments:main",
        ":internal:main",
    ]


def test_visible_source_sets_can_be_inferred_from_classpath_outputs(tmp_path):
    workspace_dict = _workspace_dict(tmp_path)
    workspace_dict["source_sets"][":orders:main"]["project_dependencies"] = []
    workspace = GradleWorkspaceModel.from_dict(workspace_dict)

    visible = workspace.visible_source_sets(SourceSetId(project_path=":orders", name="main"))

    assert [item.key for item in visible] == [
        ":orders:main",
        ":payments:main",
    ]


def test_visible_type_index_filters_to_visible_source_sets_and_jars(tmp_path):
    workspace = GradleWorkspaceModel.from_dict(_workspace_dict(tmp_path))
    source_index = SymbolIndex.from_definitions([
        SymbolDefinition(
            qualified_name="com.app.orders.OrderService",
            container=":orders:main",
            origin_kind="source",
        ),
        SymbolDefinition(
            qualified_name="com.app.payments.PaymentGateway",
            container=":payments:main",
            origin_kind="source",
        ),
        SymbolDefinition(
            qualified_name="com.app.internal.SecretThing",
            container=":internal:main",
            origin_kind="source",
        ),
    ])
    jar_index = SymbolIndex.from_definitions([
        SymbolDefinition(
            qualified_name="com.fasterxml.jackson.databind.ObjectMapper",
            container=str(tmp_path / "libs" / "jackson.jar"),
            origin_kind="binary",
        ),
        SymbolDefinition(
            qualified_name="org.slf4j.Logger",
            container=str(tmp_path / "libs" / "slf4j.jar"),
            origin_kind="binary",
        ),
        SymbolDefinition(
            qualified_name="com.external.DirOnly",
            container=str(tmp_path / "deps" / "classes"),
            origin_kind="binary",
        ),
        SymbolDefinition(
            qualified_name="com.hidden.ExternalOnly",
            container=str(tmp_path / "libs" / "hidden.jar"),
            origin_kind="binary",
        ),
    ])
    jdk_index = SymbolIndex.from_definitions([
        SymbolDefinition(
            qualified_name="java.lang.String",
            container=str(tmp_path / "fake-jdk" / "jmods" / "java.base.jmod"),
            origin_kind="jdk",
        ),
    ])

    type_index = workspace.visible_type_index_for_file(
        tmp_path / "orders" / "src" / "main" / "java" / "com" / "app" / "orders" / "OrderService.java",
        source_index=source_index,
        binary_index=jar_index,
        jdk_index=jdk_index,
    )

    assert type_index.lookup("OrderService") == ("com.app.orders.OrderService",)
    assert type_index.lookup("PaymentGateway") == ("com.app.payments.PaymentGateway",)
    assert type_index.lookup("SecretThing") == ()
    assert type_index.lookup("ObjectMapper") == (
        "com.fasterxml.jackson.databind.ObjectMapper",
    )
    assert type_index.lookup("Logger") == ("org.slf4j.Logger",)
    assert type_index.lookup("DirOnly") == ("com.external.DirOnly",)
    assert type_index.lookup("String") == ("java.lang.String",)
    assert type_index.lookup("ExternalOnly") == ()


def test_visible_source_roots_include_generated_and_dependency_roots(tmp_path):
    workspace = GradleWorkspaceModel.from_dict(_workspace_dict(tmp_path))

    roots = workspace.visible_source_roots(SourceSetId(project_path=":orders", name="main"))

    assert roots == (
        str(tmp_path.resolve() / "orders" / "src" / "main" / "java"),
        str(tmp_path.resolve() / "orders" / "build" / "generated"),
        str(tmp_path.resolve() / "payments" / "src" / "main" / "java"),
    )


def test_visible_external_binary_entries_exclude_project_artifact_jars(tmp_path):
    workspace = GradleWorkspaceModel.from_dict(_workspace_dict(tmp_path))

    entries = workspace.visible_external_binary_entries(SourceSetId(project_path=":orders", name="main"))

    assert entries == (
        str(tmp_path.resolve() / "deps" / "classes"),
        str(tmp_path.resolve() / "libs" / "jackson.jar"),
        str(tmp_path.resolve() / "libs" / "slf4j.jar"),
    )
    assert str(tmp_path.resolve() / "payments" / "build" / "libs" / "payments.jar") not in entries
