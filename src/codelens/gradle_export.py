from __future__ import annotations

from pathlib import Path

from codelens.gradle_model import GradleWorkspaceModel
from codelens.logging_config import get_logger

EXPORT_TASK_NAME = "exportCodeLensWorkspaceModel"
INIT_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "gradle_export.init.gradle"
logger = get_logger(__name__)


def build_export_command(gradle_root: str | Path, output_path: str | Path) -> list[str]:
    gradle_root = Path(gradle_root)
    output_path = Path(output_path)
    gradlew = gradle_root / "gradlew"
    command = [
        str(gradlew),
        "-I",
        str(INIT_SCRIPT_PATH),
        EXPORT_TASK_NAME,
        f"-PcodelensOutput={output_path}",
    ]
    logger.debug("Built Gradle export command for %s -> %s", gradle_root, output_path)
    return command


def load_workspace_model(path: str | Path) -> GradleWorkspaceModel:
    workspace_path = Path(path)
    model = GradleWorkspaceModel.from_json_file(workspace_path)
    logger.info(
        "Loaded workspace model from %s with %d source sets",
        workspace_path.resolve(),
        len(model.source_sets),
    )
    return model
