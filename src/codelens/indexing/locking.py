from __future__ import annotations

import os
from pathlib import Path


class IndexLockError(RuntimeError):
    pass


class IndexLock:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> "IndexLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(
                self._path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
        except FileExistsError as exc:
            raise IndexLockError(
                f"Index is already locked: {self._path}"
            ) from exc
        os.write(self._fd, str(os.getpid()).encode("utf-8"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
