from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from codelens.indexing.faiss_repository import FaissIndexRepository
from codelens.logging_config import get_logger, log_event
from codelens.timing import Stopwatch, measure

logger = get_logger(__name__)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "do",
    "does",
    "for",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "show",
    "the",
    "to",
    "what",
    "where",
}
IMPLEMENTATION_TERMS = {
    "implemented",
    "implementation",
    "implement",
    "implements",
    "how",
    "where",
}
LOOKUP_TERMS = {
    "bean",
    "find",
    "get",
    "getbean",
    "inject",
    "lookup",
    "resolve",
    "resolvebean",
    "resolvebeanregistration",
    "resolver",
}
LOOKUP_SIGNAL_TERMS = {
    "findbean",
    "getbean",
    "lookup",
    "resolve",
    "resolvebean",
    "resolvebeanregistration",
}
QUERY_SYNONYMS = {
    "lookup": ("get", "getbean", "find", "findbean", "resolve", "resolvebean"),
    "implemented": ("implementation", "default"),
    "implementation": ("implemented", "default"),
    "bean": ("beandefinition", "beanregistration"),
}
GENERIC_INFRASTRUCTURE_NAME_TOKENS = {
    "annotation",
    "definitions",
    "provider",
    "registry",
    "scanner",
}
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_CASE_RE = re.compile(r"([a-z0-9])([A-Z])")
Payload = Mapping[str, object]
PayloadDict = dict[str, object]


class QueryEncoder(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[list[float]]]: ...


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    kind: str
    symbol: str | None
    file_path: str
    score: float
    confidence: str
    summary: str
    why_matched: tuple[str, ...]
    start_line: int | None = None
    end_line: int | None = None
    source_set: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "file_path": self.file_path,
            "score": self.score,
            "confidence": self.confidence,
            "summary": self.summary,
            "why_matched": list(self.why_matched),
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source_set": self.source_set,
        }


@dataclass(frozen=True)
class SearchResponse:
    query: str
    repo_root: str
    retrieval_version: str
    returned_count: int
    has_more: bool
    results: tuple[SearchHit, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "repo_root": self.repo_root,
            "retrieval_version": self.retrieval_version,
            "returned_count": self.returned_count,
            "has_more": self.has_more,
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class RelatedChunk:
    chunk_id: str
    kind: str
    symbol: str | None
    file_path: str
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True)
class ReadCodeResult:
    chunk_id: str
    kind: str
    symbol: str | None
    file_path: str
    start_line: int | None
    end_line: int | None
    source_text: str
    summary: str
    surrounding_context: dict[str, object]
    neighbors: tuple[RelatedChunk, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source_text": self.source_text,
            "summary": self.summary,
            "surrounding_context": self.surrounding_context,
            "neighbors": [neighbor.to_dict() for neighbor in self.neighbors],
        }


class RetrievalSearchService:
    def __init__(
        self,
        repository: FaissIndexRepository,
        encoder: QueryEncoder,
    ) -> None:
        self._repository: FaissIndexRepository = repository
        self._encoder: QueryEncoder = encoder

    def search_code(
        self,
        query: str,
        *,
        repo_root: str,
        top_k: int = 5,
        kind: str | None = None,
        source_set: str | None = None,
        file_path: str | None = None,
    ) -> SearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            return SearchResponse(
                query=query,
                repo_root=repo_root,
                retrieval_version="v1",
                returned_count=0,
                has_more=False,
                results=(),
            )

        total_timer = Stopwatch.start()
        log_event(
            logger,
            level=logging.INFO,
            message="Starting retrieval search",
            repo_root=repo_root,
            query=normalized_query,
            top_k=top_k,
            kind=kind,
            source_set=source_set,
            file_path=file_path,
        )

        with measure() as embed_timer:
            query_vectors = self._encoder.embed_queries([normalized_query])[0]
        with measure() as search_timer:
            raw_matches = self._repository.search(
                query_vectors,
                top_k=max(top_k * 10, 50),
                kind=kind,
                source_set=source_set,
                file_path=file_path,
            )
        with measure() as rerank_timer:
            ranked_matches = sorted(
                (
                    self._build_hit(normalized_query, payload, semantic_score)
                    for payload, semantic_score in raw_matches
                ),
                key=lambda hit: (-hit.score, hit.chunk_id),
            )
        limited_hits = tuple(ranked_matches[:top_k])
        response = SearchResponse(
            query=query,
            repo_root=repo_root,
            retrieval_version="v1",
            returned_count=len(limited_hits),
            has_more=len(ranked_matches) > top_k,
            results=limited_hits,
        )
        log_event(
            logger,
            level=logging.INFO,
            message="Finished retrieval search",
            repo_root=repo_root,
            query=normalized_query,
            embed_ms=embed_timer.elapsed_ms,
            search_ms=search_timer.elapsed_ms,
            rerank_ms=rerank_timer.elapsed_ms,
            total_ms=total_timer.elapsed_ms,
            returned_count=response.returned_count,
            has_more=response.has_more,
        )
        return response

    def read_code(
        self,
        chunk_id: str,
        *,
        include_surrounding: bool = True,
        neighbor_limit: int = 2,
    ) -> ReadCodeResult | None:
        total_timer = Stopwatch.start()
        log_event(
            logger,
            level=logging.INFO,
            message="Starting retrieval read",
            chunk_id=chunk_id,
            include_surrounding=include_surrounding,
            neighbor_limit=neighbor_limit,
        )
        with measure() as load_timer:
            loaded = self._repository.load()
        if loaded is None:
            return None

        chunk = loaded.chunks.get(chunk_id)
        if chunk is None:
            return None

        payload = dict(chunk.payload)
        neighbors: tuple[RelatedChunk, ...] = ()
        if include_surrounding:
            with measure() as neighbor_timer:
                neighbors = self._neighbor_chunks(
                    payload,
                    candidates=[dict(item.payload) for item in loaded.chunks.values()],
                    limit=neighbor_limit,
                )
        else:
            neighbor_timer = Stopwatch.start()
            neighbor_timer.stop()

        result = ReadCodeResult(
            chunk_id=str(payload["chunk_id"]),
            kind=str(payload.get("chunk_kind") or "unknown"),
            symbol=_qualified_symbol(payload),
            file_path=str(payload.get("file_path") or ""),
            start_line=_optional_int(payload.get("start_line")),
            end_line=_optional_int(payload.get("end_line")),
            source_text=str(payload.get("source_text") or ""),
            summary=_summary_text(payload),
            surrounding_context={
                "package_name": payload.get("package"),
                "owner_chain": list(payload.get("owner_chain", [])),
                "source_set": payload.get("source_set"),
                "indexed_at": payload.get("indexed_at"),
            },
            neighbors=neighbors,
        )
        log_event(
            logger,
            level=logging.INFO,
            message="Finished retrieval read",
            chunk_id=chunk_id,
            load_ms=load_timer.elapsed_ms,
            neighbor_ms=neighbor_timer.elapsed_ms,
            total_ms=total_timer.elapsed_ms,
            neighbors=len(result.neighbors),
        )
        return result

    def _build_hit(
        self,
        query: str,
        payload: Payload,
        semantic_score: float,
    ) -> SearchHit:
        reasons = ["semantic"]
        score = semantic_score
        normalized_query = query.casefold()
        query_terms = _significant_query_terms(query)
        expanded_query_terms = _expand_query_terms(query_terms)
        query_token_set = set(expanded_query_terms)
        symbol = _qualified_symbol(payload)
        name = str(payload.get("name") or "")
        signature = str(payload.get("signature") or "")
        retrieval_text = str(payload.get("retrieval_text") or "")
        source_text = str(payload.get("source_text") or "")
        file_path = str(payload.get("file_path") or "")
        chunk_kind = str(payload.get("chunk_kind") or "unknown")
        modifiers = {item.casefold() for item in _string_list(payload.get("modifiers"))}

        name_tokens = set(_tokenize_text(name))
        symbol_tokens = set(_tokenize_text(symbol or ""))
        signature_tokens = set(_tokenize_text(signature))
        file_tokens = set(_tokenize_text(file_path))
        retrieval_tokens = set(_tokenize_text(retrieval_text))

        if name and normalized_query == name.casefold():
            score += 1.0
            reasons.append(f"name:{name}")
        elif name and name.casefold() in normalized_query:
            score += 0.4
            reasons.append(f"name_fragment:{name}")

        if symbol and symbol.casefold() in normalized_query:
            score += 0.8
            reasons.append(f"symbol:{symbol}")

        if signature and normalized_query in signature.casefold():
            score += 0.6
            reasons.append("signature")

        matched_name_terms = sorted(query_token_set & name_tokens)
        if matched_name_terms:
            score += 0.45 * len(matched_name_terms)
            reasons.append(f"name_terms:{', '.join(matched_name_terms)}")

        matched_symbol_terms = sorted(query_token_set & symbol_tokens)
        if matched_symbol_terms:
            score += 0.3 * len(matched_symbol_terms)
            reasons.append(f"symbol_terms:{', '.join(matched_symbol_terms)}")

        matched_signature_terms = sorted(query_token_set & signature_tokens)
        if matched_signature_terms:
            score += 0.2 * min(len(matched_signature_terms), 3)
            reasons.append(f"signature_terms:{', '.join(matched_signature_terms)}")

        matched_path_terms = sorted(query_token_set & file_tokens)
        if matched_path_terms:
            score += 0.2 * min(len(matched_path_terms), 3)
            reasons.append(f"path_terms:{', '.join(matched_path_terms)}")

        matched_retrieval_terms = sorted(query_token_set & retrieval_tokens)
        if matched_retrieval_terms:
            score += 0.12 * min(len(matched_retrieval_terms), 6)
            reasons.append(f"terms:{', '.join(matched_retrieval_terms)}")

        lookup_signal_terms = sorted(
            query_token_set & LOOKUP_SIGNAL_TERMS & retrieval_tokens
        )
        if lookup_signal_terms:
            score += 0.35 * len(lookup_signal_terms)
            reasons.append(f"lookup_terms:{', '.join(lookup_signal_terms)}")

        if normalized_query in retrieval_text.casefold():
            score += 0.6
            reasons.append("query_phrase")

        if _is_implementation_query(query_terms):
            score += _implementation_bias(
                chunk_kind=chunk_kind,
                source_text=source_text,
                modifiers=modifiers,
                name=name,
                matched_terms=query_token_set,
                name_tokens=name_tokens,
                retrieval_text=retrieval_text,
                reasons=reasons,
            )

        return SearchHit(
            chunk_id=str(payload["chunk_id"]),
            kind=chunk_kind,
            symbol=symbol,
            file_path=file_path,
            start_line=_optional_int(payload.get("start_line")),
            end_line=_optional_int(payload.get("end_line")),
            source_set=_optional_str(payload.get("source_set")),
            score=score,
            confidence=_confidence_label(score),
            summary=_summary_text(payload),
            why_matched=tuple(reasons),
        )

    def _neighbor_chunks(
        self,
        target_payload: Payload,
        *,
        candidates: list[PayloadDict],
        limit: int,
    ) -> tuple[RelatedChunk, ...]:
        target_chunk_id = str(target_payload["chunk_id"])
        target_file_path = target_payload.get("file_path")
        target_line = _optional_int(target_payload.get("start_line")) or 0
        same_file: list[tuple[int, PayloadDict]] = []
        for candidate in candidates:
            if str(candidate.get("chunk_id")) == target_chunk_id:
                continue
            if candidate.get("file_path") != target_file_path:
                continue
            candidate_line = _optional_int(candidate.get("start_line")) or 0
            same_file.append((abs(candidate_line - target_line), candidate))

        same_file.sort(key=lambda item: (item[0], str(item[1].get("chunk_id", ""))))
        return tuple(
            RelatedChunk(
                chunk_id=str(candidate["chunk_id"]),
                kind=str(candidate.get("chunk_kind") or "unknown"),
                symbol=_qualified_symbol(candidate),
                file_path=str(candidate.get("file_path") or ""),
                start_line=_optional_int(candidate.get("start_line")),
                end_line=_optional_int(candidate.get("end_line")),
            )
            for _, candidate in same_file[:limit]
        )


def _qualified_symbol(payload: Payload) -> str | None:
    package_name = str(payload.get("package") or "").strip()
    owner_chain = _string_list(payload.get("owner_chain"))
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    parts = [part for part in [package_name, *owner_chain, name] if part]
    return ".".join(parts) if parts else None


def _summary_text(payload: Payload) -> str:
    source_text = " ".join(str(payload.get("source_text") or "").split())
    if source_text:
        return source_text[:197] + "..." if len(source_text) > 200 else source_text
    retrieval_text = " ".join(str(payload.get("retrieval_text") or "").split())
    if len(retrieval_text) > 200:
        return retrieval_text[:197] + "..."
    return retrieval_text


def _confidence_label(score: float) -> str:
    if score >= 2.5:
        return "high"
    if score >= 1.0:
        return "medium"
    return "low"


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, tuple):
        return [str(item) for item in value]
    return []


def _significant_query_terms(query: str) -> tuple[str, ...]:
    tokens = [token for token in _tokenize_text(query) if token]
    filtered = [token for token in tokens if token not in STOPWORDS and len(token) > 1]
    if filtered:
        return tuple(dict.fromkeys(filtered))
    fallback = [token for token in tokens if len(token) > 1]
    return tuple(dict.fromkeys(fallback))


def _tokenize_text(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    decamelized = _CAMEL_CASE_RE.sub(r"\1 \2", value)
    normalized = decamelized.casefold()
    return tuple(token for token in _TOKEN_SPLIT_RE.split(normalized) if token)


def _is_implementation_query(query_terms: Sequence[str]) -> bool:
    term_set = set(query_terms)
    return bool(term_set & IMPLEMENTATION_TERMS) or bool(term_set & LOOKUP_TERMS)


def _implementation_bias(
    *,
    chunk_kind: str,
    source_text: str,
    modifiers: set[str],
    name: str,
    matched_terms: set[str],
    name_tokens: set[str],
    retrieval_text: str,
    reasons: list[str],
) -> float:
    bias = 0.0
    source_lower = source_text.casefold()
    name_lower = name.casefold()
    retrieval_lower = retrieval_text.casefold()

    if chunk_kind in {"method", "constructor", "behavior"}:
        bias += 0.7
        reasons.append("implementation:callable")
    elif chunk_kind == "type":
        if "@interface" in source_lower:
            bias -= 1.2
            reasons.append("implementation:annotation_penalty")
        elif " interface " in f" {source_lower} ":
            bias -= 0.75
            reasons.append("implementation:interface_penalty")
        elif "abstract" in modifiers or " abstract class " in f" {source_lower} ":
            bias -= 0.35
            reasons.append("implementation:abstract_penalty")
        elif any(token in source_lower for token in (" class ", " record ", " enum ")):
            bias += 0.5
            reasons.append("implementation:concrete_type")

    if name.startswith("Default"):
        bias += 0.3
        reasons.append("implementation:default_impl")

    if matched_terms & LOOKUP_TERMS:
        if any(verb in name_lower for verb in ("get", "find", "lookup", "resolve")):
            bias += 0.35
            reasons.append("implementation:resolver_name")
        if any(
            verb in retrieval_lower for verb in ("getbean", "resolve", "lookup", "find")
        ):
            bias += 0.15
            reasons.append("implementation:resolver_text")
        if chunk_kind == "type" and name_tokens & GENERIC_INFRASTRUCTURE_NAME_TOKENS:
            bias -= 0.45
            reasons.append("implementation:generic_type_penalty")
        if "provider" in name_lower:
            bias -= 0.25
            reasons.append("implementation:provider_penalty")

    return bias


def _expand_query_terms(query_terms: Sequence[str]) -> tuple[str, ...]:
    expanded: list[str] = list(query_terms)
    seen = set(expanded)
    for term in query_terms:
        for synonym in QUERY_SYNONYMS.get(term, ()):
            if synonym not in seen:
                expanded.append(synonym)
                seen.add(synonym)
    return tuple(expanded)
