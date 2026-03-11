from __future__ import annotations

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

JAVA_LANGUAGE = Language(tsjava.language())
JAVA_PARSER = Parser(JAVA_LANGUAGE)
