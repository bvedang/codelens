from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from codelens.retrieval.search import SearchHit, SearchResponse

RANKING_CONFIG_VERSION = "heuristic-rerank-v1"
CODELENS_REPO_ROOT = Path(__file__).resolve().parents[3]


class SearchService(Protocol):
    def search_code(
        self,
        query: str,
        *,
        repo_root: str,
        top_k: int = 5,
        kind: str | None = None,
        source_set: str | None = None,
        file_path: str | None = None,
    ) -> SearchResponse: ...


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    primary_paths: tuple[str, ...]
    primary_symbols: tuple[str, ...]
    primary_lines: tuple[int, ...]
    primary_line_ranges: tuple[tuple[int, int], ...] = ()
    secondary_paths: tuple[str, ...] = ()
    secondary_symbols: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalSuite:
    benchmark_version: str
    repo_name: str
    repo_commit: str
    model_name: str
    top_k: int
    line_tolerance: int
    cases: tuple[EvalCase, ...]


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    query: str
    verdict: str
    primary_rank: int | None
    matched_symbol: str | None
    matched_file: str | None
    line_hit: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvalRunResult:
    benchmark_version: str
    repo_name: str
    repo_commit: str
    model_name: str
    retrieval_version: str
    ranking_config_version: str
    codelens_git_commit: str
    codelens_git_dirty: bool
    top_k: int
    strong_passes: int
    passes: int
    near_misses: int
    fails: int
    cases: tuple[EvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cases"] = [case.to_dict() for case in self.cases]
        return payload


def load_eval_suite(path: str | Path) -> EvalSuite:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvalSuite(
        benchmark_version=str(raw["benchmark_version"]),
        repo_name=str(raw["repo_name"]),
        repo_commit=str(raw["repo_commit"]),
        model_name=str(raw["model_name"]),
        top_k=int(raw.get("top_k", 5)),
        line_tolerance=int(raw.get("line_tolerance", 25)),
        cases=tuple(
            EvalCase(
                case_id=str(item["case_id"]),
                query=str(item["query"]),
                primary_paths=tuple(item.get("primary_paths", ())),
                primary_symbols=tuple(item.get("primary_symbols", ())),
                primary_lines=tuple(
                    int(value) for value in item.get("primary_lines", ())
                ),
                primary_line_ranges=tuple(
                    (int(value[0]), int(value[1]))
                    for value in item.get("primary_line_ranges", ())
                ),
                secondary_paths=tuple(item.get("secondary_paths", ())),
                secondary_symbols=tuple(item.get("secondary_symbols", ())),
            )
            for item in raw.get("cases", ())
        ),
    )


def run_eval_suite(
    service: SearchService,
    suite: EvalSuite,
    *,
    repo_root: str,
    top_k: int | None = None,
) -> EvalRunResult:
    effective_top_k = top_k or suite.top_k
    results: list[EvalCaseResult] = []
    retrieval_versions: list[str] = []

    for case in suite.cases:
        response = service.search_code(
            case.query,
            repo_root=repo_root,
            top_k=effective_top_k,
        )
        retrieval_versions.append(response.retrieval_version)
        results.append(
            _evaluate_case(case, response.results, line_tolerance=suite.line_tolerance)
        )

    strong_passes = sum(1 for case in results if case.verdict == "strong_pass")
    passes = sum(1 for case in results if case.verdict == "pass")
    near_misses = sum(1 for case in results if case.verdict == "near_miss")
    fails = sum(1 for case in results if case.verdict == "fail")
    retrieval_version = _coalesce_retrieval_version(retrieval_versions)

    return EvalRunResult(
        benchmark_version=suite.benchmark_version,
        repo_name=suite.repo_name,
        repo_commit=suite.repo_commit,
        model_name=suite.model_name,
        retrieval_version=retrieval_version,
        ranking_config_version=RANKING_CONFIG_VERSION,
        codelens_git_commit=_current_git_commit(),
        codelens_git_dirty=_is_git_dirty(),
        top_k=effective_top_k,
        strong_passes=strong_passes,
        passes=passes,
        near_misses=near_misses,
        fails=fails,
        cases=tuple(results),
    )


def _evaluate_case(
    case: EvalCase,
    hits: Sequence[SearchHit],
    *,
    line_tolerance: int,
) -> EvalCaseResult:
    primary_rank: int | None = None
    matched_symbol: str | None = None
    matched_file: str | None = None
    line_hit = False
    near_miss = False

    for index, hit in enumerate(hits, start=1):
        primary_match, hit_line = _is_primary_match(
            case, hit, line_tolerance=line_tolerance
        )
        if primary_match:
            primary_rank = index
            matched_symbol = hit.symbol
            matched_file = hit.file_path
            line_hit = hit_line
            break
        if _is_near_miss(case, hit):
            near_miss = True

    if primary_rank is not None:
        verdict = "strong_pass" if primary_rank <= 3 else "pass"
    elif near_miss:
        verdict = "near_miss"
    else:
        verdict = "fail"

    return EvalCaseResult(
        case_id=case.case_id,
        query=case.query,
        verdict=verdict,
        primary_rank=primary_rank,
        matched_symbol=matched_symbol,
        matched_file=matched_file,
        line_hit=line_hit,
    )


def _is_primary_match(
    case: EvalCase,
    hit: SearchHit,
    *,
    line_tolerance: int,
) -> tuple[bool, bool]:
    symbol_match = _symbol_matches(hit.symbol, case.primary_symbols)
    file_match = hit.file_path in case.primary_paths
    has_line_targets = bool(case.primary_line_ranges or case.primary_lines)
    line_hit = _line_matches(
        hit.start_line,
        hit.end_line,
        case.primary_lines,
        case.primary_line_ranges,
        tolerance=line_tolerance,
    )

    if symbol_match and has_line_targets:
        return line_hit, line_hit
    if symbol_match:
        return True, line_hit
    if file_match and line_hit and not case.primary_symbols:
        return True, True
    if file_match and not case.primary_symbols and not has_line_targets:
        return True, False
    return False, False


def _is_near_miss(case: EvalCase, hit: SearchHit) -> bool:
    if hit.file_path in case.primary_paths:
        return True
    if _symbol_matches(hit.symbol, case.primary_symbols):
        return True
    if hit.file_path in case.secondary_paths:
        return True
    if _symbol_matches(hit.symbol, case.secondary_symbols):
        return True
    return False


def _symbol_matches(symbol: str | None, expected: Sequence[str]) -> bool:
    if not symbol:
        return False
    normalized = symbol.casefold()
    return any(item.casefold() in normalized for item in expected)


def _line_matches(
    start_line: int | None,
    end_line: int | None,
    expected_lines: Sequence[int],
    expected_ranges: Sequence[tuple[int, int]],
    *,
    tolerance: int,
) -> bool:
    if expected_ranges:
        if start_line is None:
            return False
        effective_end_line = end_line if end_line is not None else start_line
        return any(
            max(start_line, range_start) <= min(effective_end_line, range_end)
            for range_start, range_end in expected_ranges
        )
    if start_line is None or not expected_lines:
        return False
    return any(
        abs(start_line - expected_line) <= tolerance for expected_line in expected_lines
    )


def _coalesce_retrieval_version(versions: Sequence[str]) -> str:
    distinct = {value for value in versions if value}
    if not distinct:
        return "unknown"
    if len(distinct) == 1:
        return next(iter(distinct))
    return "mixed"


def _current_git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=CODELENS_REPO_ROOT,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _is_git_dirty() -> bool:
    try:
        completed = subprocess.run(
            ["git", "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
            cwd=CODELENS_REPO_ROOT,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(completed.stdout.strip())
