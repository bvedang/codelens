from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from codelens.constants import TYPE_NODES
from codelens.parser import JAVA_PARSER
from codelens.type_resolver import TypeIndex

parser = JAVA_PARSER


def _text(code: bytes, node) -> str:
    return code[node.start_byte : node.end_byte].decode("utf-8")


def _package_name(code: bytes, root) -> str | None:
    for child in root.named_children:
        if child.type == "package_declaration":
            clean = _text(code, child).replace("package ", "", 1).rstrip(";").strip()
            return clean or None
    return None


def _class_body(node):
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    for child in node.named_children:
        if child.type == "class_body":
            return child
    return None


@dataclass(frozen=True)
class SymbolDefinition:
    qualified_name: str
    container: str | None
    origin_kind: str
    filepath: str | None = None

    @property
    def simple_name(self) -> str:
        return self.qualified_name.rsplit(".", 1)[-1]


@dataclass(frozen=True)
class SymbolIndex:
    definitions: tuple[SymbolDefinition, ...]

    @classmethod
    def empty(cls) -> "SymbolIndex":
        return cls(definitions=())

    @classmethod
    def from_definitions(cls, definitions: Iterable[SymbolDefinition]) -> "SymbolIndex":
        deduped: dict[tuple[str, str | None, str, str | None], SymbolDefinition] = {}
        for definition in definitions:
            key = (
                definition.qualified_name,
                definition.container,
                definition.origin_kind,
                definition.filepath,
            )
            deduped[key] = definition
        return cls(
            definitions=tuple(
                sorted(
                    deduped.values(),
                    key=lambda d: (
                        d.qualified_name,
                        d.origin_kind,
                        d.container or "",
                        d.filepath or "",
                    ),
                )
            )
        )

    def merge(self, *others: "SymbolIndex") -> "SymbolIndex":
        merged = list(self.definitions)
        for other in others:
            merged.extend(other.definitions)
        return SymbolIndex.from_definitions(merged)

    def lookup(self, simple_name: str) -> tuple[SymbolDefinition, ...]:
        return tuple(
            definition
            for definition in self.definitions
            if definition.simple_name == simple_name
        )

    def qualified_names(
        self, origin_kind: str | None = None, containers: Iterable[str] | None = None
    ) -> tuple[str, ...]:
        allowed = set(containers) if containers is not None else None
        names = {
            definition.qualified_name
            for definition in self.definitions
            if (origin_kind is None or definition.origin_kind == origin_kind)
            and (allowed is None or definition.container in allowed)
        }
        return tuple(sorted(names))

    def to_type_index(
        self, origin_kind: str | None = None, containers: Iterable[str] | None = None
    ) -> TypeIndex:
        return TypeIndex.from_qualified_names(
            self.qualified_names(origin_kind=origin_kind, containers=containers)
        )


def build_source_symbol_index(
    paths: Iterable[str | Path], source_set_lookup=None
) -> SymbolIndex:
    definitions: list[SymbolDefinition] = []
    for path in paths:
        current = Path(path)
        if current.is_dir():
            java_files = current.rglob("*.java")
        else:
            java_files = [current]

        for java_file in java_files:
            if java_file.suffix != ".java":
                continue
            code = java_file.read_bytes()
            tree = parser.parse(code)
            root = tree.root_node
            package_name = _package_name(code, root)
            container = source_set_lookup(java_file) if source_set_lookup else None
            definitions.extend(
                _collect_source_symbols(
                    code,
                    root,
                    package_name=package_name,
                    container=container,
                    filepath=str(java_file),
                )
            )
    return SymbolIndex.from_definitions(definitions)


def _collect_source_symbols(
    code: bytes, root, package_name: str | None, container: str | None, filepath: str
) -> list[SymbolDefinition]:
    definitions: list[SymbolDefinition] = []

    def visit(node, owner_chain: list[str]) -> None:
        if node.type not in TYPE_NODES:
            return

        name = node.child_by_field_name("name")
        if not name:
            return

        simple_name = _text(code, name)
        qualified_name = ".".join(
            [part for part in [package_name, *owner_chain, simple_name] if part]
        )
        definitions.append(
            SymbolDefinition(
                qualified_name=qualified_name,
                container=container,
                origin_kind="source",
                filepath=filepath,
            )
        )

        body = _class_body(node)
        if body is None:
            return
        for child in body.named_children:
            if child.type in TYPE_NODES:
                visit(child, owner_chain + [simple_name])

    for child in root.named_children:
        if child.type in TYPE_NODES:
            visit(child, [])

    return definitions


def build_jar_symbol_index(jar_paths: Iterable[str | Path]) -> SymbolIndex:
    return build_binary_symbol_index(jar_paths)


def build_binary_symbol_index(paths: Iterable[str | Path]) -> SymbolIndex:
    definitions: list[SymbolDefinition] = []
    for path in paths:
        current = Path(path)
        if current.is_dir():
            definitions.extend(_binary_definitions_from_directory(current))
            continue
        if current.suffix == ".jar":
            definitions.extend(_binary_definitions_from_jar(current))
    return SymbolIndex.from_definitions(definitions)


def build_jdk_symbol_index(jdk_home: str | Path) -> SymbolIndex:
    jmods_dir = _find_jmods_dir(Path(jdk_home))
    definitions: list[SymbolDefinition] = []
    for jmod_path in sorted(jmods_dir.glob("*.jmod")):
        with ZipFile(jmod_path) as jmod:
            for entry in jmod.namelist():
                qualified_name = _qualified_name_from_jmod_entry(entry)
                if qualified_name is None:
                    continue
                definitions.append(
                    SymbolDefinition(
                        qualified_name=qualified_name,
                        container=str(jmod_path),
                        origin_kind="jdk",
                        filepath=None,
                    )
                )
    return SymbolIndex.from_definitions(definitions)


def _binary_definitions_from_jar(jar_path: Path) -> list[SymbolDefinition]:
    definitions: list[SymbolDefinition] = []
    with ZipFile(jar_path) as jar:
        for entry in jar.namelist():
            qualified_name = _qualified_name_from_class_entry(entry)
            if qualified_name is None:
                continue
            definitions.append(
                SymbolDefinition(
                    qualified_name=qualified_name,
                    container=str(jar_path),
                    origin_kind="binary",
                    filepath=None,
                )
            )
    return definitions


def _binary_definitions_from_directory(directory: Path) -> list[SymbolDefinition]:
    definitions: list[SymbolDefinition] = []
    for class_file in directory.rglob("*.class"):
        relative_entry = class_file.relative_to(directory).as_posix()
        qualified_name = _qualified_name_from_class_entry(relative_entry)
        if qualified_name is None:
            continue
        definitions.append(
            SymbolDefinition(
                qualified_name=qualified_name,
                container=str(directory),
                origin_kind="binary",
                filepath=None,
            )
        )
    return definitions


def _find_jmods_dir(jdk_home: Path) -> Path:
    candidates = [jdk_home / "jmods", jdk_home / "Contents" / "Home" / "jmods"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"Could not find jmods directory under {jdk_home}")


def _qualified_name_from_jmod_entry(entry: str) -> str | None:
    if not entry.startswith("classes/"):
        return None
    return _qualified_name_from_class_entry(entry[len("classes/") :])


def _qualified_name_from_class_entry(entry: str) -> str | None:
    if not entry.endswith(".class"):
        return None
    if entry.endswith("module-info.class") or entry.endswith("package-info.class"):
        return None

    raw_name = entry[:-6].replace("/", ".")
    parts = raw_name.split(".")
    if not parts:
        return None

    class_name = parts[-1]
    inner_parts = class_name.split("$")
    if not inner_parts:
        return None
    if any(part.isdigit() for part in inner_parts[1:]):
        return None

    normalized_class = ".".join(part for part in inner_parts if part)
    return ".".join(parts[:-1] + [normalized_class])
