from __future__ import annotations

WORKSPACE_SCHEMA_VERSION = 1


def validate_workspace_export(data: object, require_schema_version: bool) -> None:
    if not isinstance(data, dict):
        raise ValueError("Workspace export must be a JSON object")

    schema_version = data.get("schema_version")
    if require_schema_version and schema_version != WORKSPACE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported workspace schema_version: {schema_version!r}; "
            f"expected {WORKSPACE_SCHEMA_VERSION}"
        )
    if schema_version is not None and schema_version != WORKSPACE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported workspace schema_version: {schema_version!r}; "
            f"expected {WORKSPACE_SCHEMA_VERSION}"
        )

    source_sets = data.get("source_sets")
    if not isinstance(source_sets, dict):
        raise ValueError("Workspace export must contain a 'source_sets' object")

    for key, value in source_sets.items():
        if not isinstance(key, str):
            raise ValueError("Workspace source set keys must be strings")
        if not isinstance(value, dict):
            raise ValueError(f"Workspace source set {key!r} must be an object")
