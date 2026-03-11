"""Tests for gradle_export.py."""

import json
from pathlib import Path

import pytest

from codelens.gradle_export import EXPORT_TASK_NAME, build_export_command, load_workspace_model


def test_build_export_command_points_at_gradlew_and_init_script(tmp_path):
    gradle_root = tmp_path / "spring-framework"
    gradle_root.mkdir()
    (gradle_root / "gradlew").write_text("", encoding="utf-8")
    output_path = tmp_path / "workspace.json"

    command = build_export_command(gradle_root, output_path)

    assert command[0] == str(gradle_root / "gradlew")
    assert command[1:4] == ["-I", command[2], EXPORT_TASK_NAME]
    assert command[4] == f"-PcodelensOutput={output_path}"
    assert command[2].endswith("gradle_export.init.gradle")
    assert command[2] == str(Path(__file__).resolve().parents[1] / "gradle_export.init.gradle")


def test_load_workspace_model_reads_exported_json(tmp_path):
    export_path = tmp_path / "workspace.json"
    export_path.write_text(json.dumps({
        "schema_version": 1,
        "jdk_home": "/opt/jdks/temurin-21",
        "source_sets": {
            ":spring-context:main": {
                "source_roots": ["spring-context/src/main/java"],
                "generated_source_roots": [],
                "project_dependencies": [],
                "external_jars": ["libs/jackson.jar"],
                "project_artifact_entries": ["spring-core/build/libs/spring-core.jar"],
                "external_binary_entries": ["libs/jackson.jar"],
                "output_dirs": ["spring-context/build/classes/java/main"],
                "compile_classpath_entries": ["libs/jackson.jar"],
                "runtime_classpath_entries": [],
            }
        }
    }), encoding="utf-8")

    model = load_workspace_model(export_path)

    assert ":spring-context:main" in model.source_sets
    assert model.jdk_home == "/opt/jdks/temurin-21"
    assert model.source_sets[":spring-context:main"].output_dirs == (
        "spring-context/build/classes/java/main",
    )
    assert model.source_sets[":spring-context:main"].external_binary_entries == (
        "libs/jackson.jar",
    )


def test_load_workspace_model_requires_schema_version(tmp_path):
    export_path = tmp_path / "workspace.json"
    export_path.write_text(json.dumps({
        "source_sets": {},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        load_workspace_model(export_path)
