from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from codelens.symbol_index import SymbolIndex
from codelens.type_resolver import TypeIndex
from codelens.workspace_schema import validate_workspace_export


def _normalize_path(path: str | Path) -> Path:
    return Path(path).resolve(strict=False)


@dataclass(frozen=True)
class SourceSetId:
    project_path: str
    name: str

    @property
    def key(self) -> str:
        return f"{self.project_path}:{self.name}"

    @classmethod
    def from_key(cls, key: str) -> "SourceSetId":
        project_path, separator, name = key.rpartition(":")
        if not separator or not project_path or not name:
            raise ValueError(f"Invalid source set key: {key!r}")
        return cls(project_path=project_path, name=name)


@dataclass(frozen=True)
class SourceSetModel:
    source_set_id: SourceSetId
    source_roots: tuple[str, ...]
    generated_source_roots: tuple[str, ...]
    project_dependencies: tuple[SourceSetId, ...]
    external_jars: tuple[str, ...]
    project_artifact_entries: tuple[str, ...] = ()
    external_binary_entries: tuple[str, ...] = ()
    output_dirs: tuple[str, ...] = ()
    compile_classpath_entries: tuple[str, ...] = ()
    runtime_classpath_entries: tuple[str, ...] = ()

    @property
    def all_roots(self) -> tuple[str, ...]:
        return self.source_roots + self.generated_source_roots


@dataclass(frozen=True)
class GradleWorkspaceModel:
    source_sets: Mapping[str, SourceSetModel]
    jdk_home: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "GradleWorkspaceModel":
        validate_workspace_export(data, require_schema_version=False)
        raw_source_sets = data.get("source_sets", {})
        source_sets: dict[str, SourceSetModel] = {}
        for key, raw in raw_source_sets.items():
            source_set_id = SourceSetId.from_key(key)
            source_sets[key] = SourceSetModel(
                source_set_id=source_set_id,
                source_roots=tuple(raw.get("source_roots", [])),
                generated_source_roots=tuple(raw.get("generated_source_roots", [])),
                project_dependencies=tuple(
                    SourceSetId.from_key(dep_key)
                    for dep_key in raw.get("project_dependencies", [])
                ),
                external_jars=tuple(raw.get("external_jars", [])),
                project_artifact_entries=tuple(raw.get("project_artifact_entries", [])),
                external_binary_entries=tuple(
                    raw.get("external_binary_entries", raw.get("external_jars", []))
                ),
                output_dirs=tuple(raw.get("output_dirs", [])),
                compile_classpath_entries=tuple(raw.get("compile_classpath_entries", [])),
                runtime_classpath_entries=tuple(raw.get("runtime_classpath_entries", [])),
            )
        return cls(
            source_sets=source_sets,
            jdk_home=data.get("jdk_home"),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "GradleWorkspaceModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        validate_workspace_export(data, require_schema_version=True)
        return cls.from_dict(data)

    def source_set_for_file(self, filepath: str | Path) -> SourceSetId | None:
        file_path = _normalize_path(filepath)
        matched: list[tuple[int, SourceSetId]] = []
        for model in self.source_sets.values():
            for root in model.all_roots:
                root_path = _normalize_path(root)
                try:
                    file_path.relative_to(root_path)
                except ValueError:
                    continue
                matched.append((len(root_path.parts), model.source_set_id))
        if not matched:
            return None
        return max(matched, key=lambda item: item[0])[1]

    def visible_source_sets(self, source_set_id: SourceSetId) -> tuple[SourceSetId, ...]:
        root_key = source_set_id.key
        if root_key not in self.source_sets:
            return ()

        output_lookup = self._output_dir_lookup()
        seen: set[str] = set()
        ordered: list[SourceSetId] = []

        def visit(current_key: str) -> None:
            if current_key in seen:
                return
            seen.add(current_key)
            model = self.source_sets.get(current_key)
            if model is None:
                return
            ordered.append(model.source_set_id)
            for dep in model.project_dependencies:
                visit(dep.key)
            for inferred_dep in self._source_sets_from_classpath(model, output_lookup):
                visit(inferred_dep.key)

        visit(root_key)
        return tuple(ordered)

    def visible_external_binary_entries(self, source_set_id: SourceSetId) -> tuple[str, ...]:
        binaries: set[str] = set()
        for visible_source_set in self.visible_source_sets(source_set_id):
            model = self.source_sets.get(visible_source_set.key)
            if model is None:
                continue
            binaries.update(model.external_binary_entries)
        return tuple(sorted(binaries))

    def visible_project_artifact_entries(self, source_set_id: SourceSetId) -> tuple[str, ...]:
        artifacts: set[str] = set()
        for visible_source_set in self.visible_source_sets(source_set_id):
            model = self.source_sets.get(visible_source_set.key)
            if model is None:
                continue
            artifacts.update(model.project_artifact_entries)
        return tuple(sorted(artifacts))

    def visible_external_jars(self, source_set_id: SourceSetId) -> tuple[str, ...]:
        return tuple(
            entry for entry in self.visible_external_binary_entries(source_set_id)
            if entry.endswith(".jar")
        )

    def visible_source_roots(self, source_set_id: SourceSetId) -> tuple[str, ...]:
        roots: list[str] = []
        seen: set[str] = set()
        for visible_source_set in self.visible_source_sets(source_set_id):
            model = self.source_sets.get(visible_source_set.key)
            if model is None:
                continue
            for root in model.all_roots:
                normalized = str(_normalize_path(root))
                if normalized in seen:
                    continue
                seen.add(normalized)
                roots.append(root)
        return tuple(roots)

    def visible_source_roots_for_file(self, filepath: str | Path) -> tuple[str, ...]:
        source_set_id = self.source_set_for_file(filepath)
        if source_set_id is None:
            return ()
        return self.visible_source_roots(source_set_id)

    def visible_type_index_for_file(self, filepath: str | Path,
                                    source_index: SymbolIndex,
                                    binary_index: SymbolIndex | None = None,
                                    jdk_index: SymbolIndex | None = None) -> TypeIndex:
        source_set_id = self.source_set_for_file(filepath)
        return self.visible_type_index(
            source_set_id,
            source_index=source_index,
            binary_index=binary_index,
            jdk_index=jdk_index,
        )

    def visible_type_index(
        self,
        source_set_id: SourceSetId | None,
        *,
        source_index: SymbolIndex,
        binary_index: SymbolIndex | None = None,
        jdk_index: SymbolIndex | None = None,
    ) -> TypeIndex:
        if source_set_id is None:
            return TypeIndex.empty()

        visible_source_keys = [item.key for item in self.visible_source_sets(source_set_id)]
        qualified_names = set(
            source_index.qualified_names(origin_kind="source", containers=visible_source_keys)
        )

        if binary_index is not None:
            visible_binaries = self.visible_external_binary_entries(source_set_id)
            qualified_names.update(
                binary_index.qualified_names(origin_kind="binary", containers=visible_binaries)
            )

        if jdk_index is not None:
            qualified_names.update(jdk_index.qualified_names(origin_kind="jdk"))

        return TypeIndex.from_qualified_names(qualified_names)

    def source_set_lookup(self, filepath: str | Path) -> str | None:
        source_set_id = self.source_set_for_file(filepath)
        return source_set_id.key if source_set_id else None

    def _output_dir_lookup(self) -> dict[str, SourceSetId]:
        lookup: dict[str, SourceSetId] = {}
        for model in self.source_sets.values():
            for output_dir in model.output_dirs:
                lookup[str(_normalize_path(output_dir))] = model.source_set_id
        return lookup

    def _source_sets_from_classpath(self, model: SourceSetModel,
                                    output_lookup: Mapping[str, SourceSetId]) -> tuple[SourceSetId, ...]:
        inferred: list[SourceSetId] = []
        for entry in model.compile_classpath_entries + model.runtime_classpath_entries:
            source_set_id = output_lookup.get(str(_normalize_path(entry)))
            if source_set_id is None or source_set_id == model.source_set_id:
                continue
            inferred.append(source_set_id)
        seen = set()
        ordered: list[SourceSetId] = []
        for item in inferred:
            if item.key in seen:
                continue
            seen.add(item.key)
            ordered.append(item)
        return tuple(ordered)
