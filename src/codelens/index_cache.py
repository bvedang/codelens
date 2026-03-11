from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

from codelens.logging_config import get_logger
from codelens.symbol_index import (
    SymbolIndex,
    build_binary_symbol_index,
    build_jdk_symbol_index,
    build_source_symbol_index,
)

logger = get_logger(__name__)


def _hash_parts(parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _snapshot_files(paths: Iterable[Path], pattern: str) -> tuple[tuple[str, int, int], ...]:
    files: list[tuple[str, int, int]] = []
    for path in sorted(Path(p).resolve() for p in paths):
        if not path.exists():
            continue
        if path.is_file():
            if path.match(pattern):
                files.append(_file_signature(path))
            continue
        for child in sorted(path.rglob(pattern)):
            if child.is_file():
                files.append(_file_signature(child))
    return tuple(files)


def source_index_fingerprint(paths: Iterable[str | Path], context_token: str | None = None) -> str:
    normalized = [Path(path).resolve() for path in paths]
    return _hash_parts({
        "kind": "source",
        "context_token": context_token,
        "roots": [str(path) for path in normalized],
        "files": _snapshot_files(normalized, "*.java"),
    })


def binary_index_fingerprint(paths: Iterable[str | Path]) -> str:
    normalized = [Path(path).resolve() for path in paths]
    jar_files = [path for path in normalized if path.exists() and path.is_file()]
    class_dirs = [path for path in normalized if path.exists() and path.is_dir()]
    return _hash_parts({
        "kind": "binary",
        "jars": [_file_signature(path) for path in jar_files],
        "classes": _snapshot_files(class_dirs, "*.class"),
    })


def jdk_index_fingerprint(jdk_home: str | Path) -> str:
    resolved_home = Path(jdk_home).resolve()
    candidate_dirs = [
        resolved_home / "jmods",
        resolved_home / "Contents" / "Home" / "jmods",
    ]
    jmods_dir = next((path for path in candidate_dirs if path.is_dir()), None)
    if jmods_dir is None:
        raise FileNotFoundError(f"Could not find jmods directory under {resolved_home}")

    return _hash_parts({
        "kind": "jdk",
        "jdk_home": str(resolved_home),
        "jmods": _snapshot_files([jmods_dir], "*.jmod"),
    })


class IndexCache:
    def __init__(self) -> None:
        self._source_indexes: dict[str, SymbolIndex] = {}
        self._binary_indexes: dict[str, SymbolIndex] = {}
        self._jdk_indexes: dict[str, SymbolIndex] = {}

    def get_source_index(
        self,
        paths: Iterable[str | Path],
        *,
        source_set_lookup: Callable | None = None,
        context_token: str | None = None,
    ) -> SymbolIndex:
        path_list = tuple(paths)
        if not path_list:
            return SymbolIndex.empty()
        fingerprint = source_index_fingerprint(path_list, context_token=context_token)
        cached = self._source_indexes.get(fingerprint)
        if cached is not None:
            logger.info("Reusing source index from cache for %d roots", len(path_list))
            return cached
        logger.info("Building source index for %d roots", len(path_list))
        index = build_source_symbol_index(path_list, source_set_lookup=source_set_lookup)
        self._source_indexes[fingerprint] = index
        return index

    def get_binary_index(self, paths: Iterable[str | Path]) -> SymbolIndex:
        path_list = tuple(paths)
        if not path_list:
            return SymbolIndex.empty()
        fingerprint = binary_index_fingerprint(path_list)
        cached = self._binary_indexes.get(fingerprint)
        if cached is not None:
            logger.info("Reusing binary index from cache for %d paths", len(path_list))
            return cached
        logger.info("Building binary index for %d paths", len(path_list))
        index = build_binary_symbol_index(path_list)
        self._binary_indexes[fingerprint] = index
        return index

    def get_jdk_index(self, jdk_home: str | Path) -> SymbolIndex:
        fingerprint = jdk_index_fingerprint(jdk_home)
        cached = self._jdk_indexes.get(fingerprint)
        if cached is not None:
            logger.info("Reusing JDK index from cache for %s", Path(jdk_home).resolve())
            return cached
        logger.info("Building JDK index for %s", Path(jdk_home).resolve())
        index = build_jdk_symbol_index(jdk_home)
        self._jdk_indexes[fingerprint] = index
        return index
