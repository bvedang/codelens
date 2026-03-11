from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from codelens.constants import TYPE_NODES
from codelens.parser import JAVA_PARSER

parser = JAVA_PARSER

_TYPE_HEAD_RE = re.compile(r"^([A-Za-z_][$\w]*)(.*)$")


def _text(code: bytes, node) -> str:
    return code[node.start_byte : node.end_byte].decode("utf-8")


def _package_name(package_declaration: str | None) -> str | None:
    if not package_declaration:
        return None
    clean = package_declaration.replace("package ", "", 1).rstrip(";").strip()
    return clean or None


def _qualified_package_name(qualified_name: str) -> str | None:
    parts = qualified_name.rsplit(".", 1)
    if len(parts) != 2:
        return None
    return parts[0]


def _with_suffix(qualified_head: str, suffix: str) -> str:
    return qualified_head + suffix


@dataclass(frozen=True)
class ImportContext:
    package_name: str | None
    explicit_imports: Mapping[str, str]
    wildcard_imports: tuple[str, ...]

    @classmethod
    def from_declarations(cls,
                          package_declaration: str | None,
                          imports: Iterable[str]) -> "ImportContext":
        explicit_imports: dict[str, str] = {}
        wildcard_imports: list[str] = []

        for declaration in imports:
            clean = declaration.replace("import ", "", 1).rstrip(";").strip()
            if clean.startswith("static "):
                continue
            if clean.endswith(".*"):
                wildcard_imports.append(clean[:-2])
                continue
            simple_name = clean.rsplit(".", 1)[-1]
            explicit_imports[simple_name] = clean

        return cls(
            package_name=_package_name(package_declaration),
            explicit_imports=explicit_imports,
            wildcard_imports=tuple(wildcard_imports),
        )


@dataclass(frozen=True)
class TypeResolution:
    source_name: str | None
    resolved_name: str | None
    strategy: str
    candidates: tuple[str, ...] = ()

    def best_name(self) -> str | None:
        return self.resolved_name or self.source_name


@dataclass(frozen=True)
class TypeIndex:
    by_simple_name: Mapping[str, tuple[str, ...]]

    @classmethod
    def empty(cls) -> "TypeIndex":
        return cls(by_simple_name={})

    @classmethod
    def from_qualified_names(cls, qualified_names: Iterable[str]) -> "TypeIndex":
        grouped: dict[str, set[str]] = {}
        for qualified_name in qualified_names:
            simple_name = qualified_name.rsplit(".", 1)[-1]
            grouped.setdefault(simple_name, set()).add(qualified_name)
        return cls(
            by_simple_name={
                simple_name: tuple(sorted(names))
                for simple_name, names in grouped.items()
            }
        )

    def lookup(self, simple_name: str) -> tuple[str, ...]:
        return self.by_simple_name.get(simple_name, ())


def build_project_type_index(paths: Iterable[str | Path]) -> TypeIndex:
    qualified_names: set[str] = set()
    for path in paths:
        current = Path(path)
        if current.is_dir():
            file_iter = current.rglob("*.java")
        else:
            file_iter = [current]

        for java_file in file_iter:
            if java_file.suffix != ".java":
                continue
            code = java_file.read_bytes()
            tree = parser.parse(code)
            root = tree.root_node

            package_declaration = None
            for child in root.named_children:
                if child.type == "package_declaration":
                    package_declaration = _text(code, child)
                    break

            package_name = _package_name(package_declaration)
            for child in root.named_children:
                if child.type not in TYPE_NODES:
                    continue
                name = child.child_by_field_name("name")
                if not name:
                    continue
                simple_name = _text(code, name)
                qualified_names.add(
                    f"{package_name}.{simple_name}" if package_name else simple_name
                )

    return TypeIndex.from_qualified_names(qualified_names)


class TypeResolver:
    def __init__(self, type_index: TypeIndex | None = None):
        self.type_index = type_index or TypeIndex.empty()

    def build_import_context(self,
                             package_declaration: str | None,
                             imports: Iterable[str]) -> ImportContext:
        return ImportContext.from_declarations(package_declaration, imports)

    def resolve_type_reference(self,
                               type_name: str | None,
                               import_context: ImportContext | None,
                               local_types: Mapping[str, str] | None = None) -> TypeResolution:
        if type_name is None:
            return TypeResolution(None, None, "empty")

        match = _TYPE_HEAD_RE.match(type_name)
        if not match:
            return TypeResolution(type_name, None, "unresolved")

        head, suffix = match.groups()

        if local_types and head in local_types:
            return TypeResolution(
                type_name,
                _with_suffix(local_types[head], suffix),
                "local_type",
            )

        if import_context and head in import_context.explicit_imports:
            return TypeResolution(
                type_name,
                _with_suffix(import_context.explicit_imports[head], suffix),
                "explicit_import",
            )

        same_package_match = self._resolve_same_package(head, import_context)
        if same_package_match:
            return TypeResolution(
                type_name,
                _with_suffix(same_package_match, suffix),
                "same_package",
            )

        candidate_sources: dict[str, str] = {}
        for wildcard_match in self._resolve_wildcard_imports(head, import_context):
            candidate_sources[wildcard_match] = "wildcard_import"

        java_lang_match = self._resolve_java_lang(head)
        if java_lang_match:
            candidate_sources[java_lang_match] = "java_lang"

        if not candidate_sources:
            return TypeResolution(type_name, None, "unresolved")

        if len(candidate_sources) == 1:
            qualified_name, strategy = next(iter(candidate_sources.items()))
            return TypeResolution(
                type_name,
                _with_suffix(qualified_name, suffix),
                strategy,
            )

        return TypeResolution(
            type_name,
            None,
            "ambiguous",
            candidates=tuple(
                sorted(_with_suffix(candidate, suffix) for candidate in candidate_sources)
            ),
        )

    def _resolve_same_package(self,
                              simple_name: str,
                              import_context: ImportContext | None) -> str | None:
        if not import_context or not import_context.package_name:
            return None

        matches = [
            candidate
            for candidate in self.type_index.lookup(simple_name)
            if _qualified_package_name(candidate) == import_context.package_name
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def _resolve_wildcard_imports(self,
                                  simple_name: str,
                                  import_context: ImportContext | None) -> tuple[str, ...]:
        if not import_context:
            return ()

        matches = [
            candidate
            for candidate in self.type_index.lookup(simple_name)
            if _qualified_package_name(candidate) in import_context.wildcard_imports
        ]
        return tuple(sorted(set(matches)))

    def _resolve_java_lang(self, simple_name: str) -> str | None:
        matches = [
            candidate
            for candidate in self.type_index.lookup(simple_name)
            if _qualified_package_name(candidate) == "java.lang"
        ]
        if len(matches) == 1:
            return matches[0]
        return None
