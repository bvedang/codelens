import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from codelens.storage.constants import PRAGMA_FROEIGN_KEYS, PRAGMA_WAL_MODE


@dataclass(frozen=True)
class SQLiteConfig:
    path: Path
    timeout_seconds: float = 5.0

    @classmethod
    def from_path(cls, path: str | Path, *, timeout_seconds: float = 5.0) -> Self:
        return cls(path=Path(path).resolve(), timeout_seconds=timeout_seconds)


def connect_sqlite(config: SQLiteConfig) -> sqlite3.Connection:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(config.path, timeout=config.timeout_seconds)
    connection.row_factory = sqlite3.Row
    connection.execute(PRAGMA_WAL_MODE)
    connection.execute(PRAGMA_FROEIGN_KEYS)
    return connection
