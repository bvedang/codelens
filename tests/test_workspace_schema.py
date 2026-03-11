import pytest

from codelens.workspace_schema import WORKSPACE_SCHEMA_VERSION, validate_workspace_export


def test_validate_workspace_export_accepts_current_schema():
    validate_workspace_export(
        {
            "schema_version": WORKSPACE_SCHEMA_VERSION,
            "source_sets": {
                ":app:main": {},
            },
        },
        require_schema_version=True,
    )


def test_validate_workspace_export_rejects_wrong_schema():
    with pytest.raises(ValueError, match="Unsupported workspace schema_version"):
        validate_workspace_export(
            {
                "schema_version": WORKSPACE_SCHEMA_VERSION + 1,
                "source_sets": {
                    ":app:main": {},
                },
            },
            require_schema_version=True,
        )
