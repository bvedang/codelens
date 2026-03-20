import json
from types import SimpleNamespace

import codelens.retrieval.eval as eval_module
from codelens.retrieval.eval import load_eval_suite, run_eval_suite
from codelens.retrieval.search import SearchHit, SearchResponse


class _FakeSearchService:
    def __init__(self, responses):
        self._responses = responses

    def search_code(
        self, query, *, repo_root, top_k=5, kind=None, source_set=None, file_path=None
    ):
        return self._responses[query]


def test_run_eval_suite_scores_primary_hits_and_near_misses(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "benchmark_version": "test-v1",
                "repo_name": "demo",
                "repo_commit": "abc123",
                "model_name": "fake-model",
                "top_k": 5,
                "line_tolerance": 10,
                "cases": [
                    {
                        "case_id": "primary",
                        "query": "find primary",
                        "primary_paths": ["src/Target.java"],
                        "primary_symbols": ["Target.run"],
                        "primary_line_ranges": [[40, 45]],
                        "primary_lines": [40],
                    },
                    {
                        "case_id": "near",
                        "query": "find near",
                        "primary_paths": ["src/Target.java"],
                        "primary_symbols": ["Target.run"],
                        "primary_lines": [40],
                        "secondary_paths": ["src/Nearby.java"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    suite = load_eval_suite(fixture)
    service = _FakeSearchService(
        {
            "find primary": SearchResponse(
                query="find primary",
                repo_root="/tmp/repo",
                retrieval_version="v1",
                returned_count=1,
                has_more=False,
                results=(
                    SearchHit(
                        chunk_id="chunk-1",
                        kind="method",
                        symbol="com.app.Target.run",
                        file_path="src/Target.java",
                        start_line=42,
                        end_line=50,
                        source_set=None,
                        score=1.0,
                        confidence="high",
                        summary="run target",
                        why_matched=("semantic",),
                    ),
                ),
            ),
            "find near": SearchResponse(
                query="find near",
                repo_root="/tmp/repo",
                retrieval_version="v1",
                returned_count=1,
                has_more=False,
                results=(
                    SearchHit(
                        chunk_id="chunk-2",
                        kind="type",
                        symbol="com.app.Nearby",
                        file_path="src/Nearby.java",
                        start_line=10,
                        end_line=20,
                        source_set=None,
                        score=1.0,
                        confidence="high",
                        summary="nearby",
                        why_matched=("semantic",),
                    ),
                ),
            ),
        }
    )
    monkeypatch.setattr(eval_module, "_current_git_commit", lambda: "deadbeef")
    monkeypatch.setattr(eval_module, "_is_git_dirty", lambda: False)

    result = run_eval_suite(service, suite, repo_root="/tmp/repo")

    assert result.retrieval_version == "v1"
    assert result.ranking_config_version == eval_module.RANKING_CONFIG_VERSION
    assert result.codelens_git_commit == "deadbeef"
    assert result.codelens_git_dirty is False
    assert result.strong_passes == 1
    assert result.near_misses == 1
    assert result.fails == 0
    assert result.cases[0].verdict == "strong_pass"
    assert result.cases[0].line_hit is True
    assert result.cases[1].verdict == "near_miss"


def test_run_eval_suite_requires_line_hit_for_symbol_matches_when_lines_are_pinned(
    tmp_path, monkeypatch
):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "benchmark_version": "test-v1",
                "repo_name": "demo",
                "repo_commit": "abc123",
                "model_name": "fake-model",
                "top_k": 5,
                "line_tolerance": 5,
                "cases": [
                    {
                        "case_id": "wrong-overload",
                        "query": "find target",
                        "primary_paths": ["src/Target.java"],
                        "primary_symbols": ["Target.run"],
                        "primary_line_ranges": [[80, 90]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    suite = load_eval_suite(fixture)
    service = _FakeSearchService(
        {
            "find target": SearchResponse(
                query="find target",
                repo_root="/tmp/repo",
                retrieval_version="v1",
                returned_count=1,
                has_more=False,
                results=(
                    SearchHit(
                        chunk_id="chunk-1",
                        kind="method",
                        symbol="com.app.Target.run",
                        file_path="src/Target.java",
                        start_line=20,
                        end_line=30,
                        source_set=None,
                        score=1.0,
                        confidence="high",
                        summary="wrong overload",
                        why_matched=("semantic",),
                    ),
                ),
            )
        }
    )
    monkeypatch.setattr(eval_module, "_current_git_commit", lambda: "deadbeef")
    monkeypatch.setattr(eval_module, "_is_git_dirty", lambda: False)

    result = run_eval_suite(service, suite, repo_root="/tmp/repo")

    assert result.strong_passes == 0
    assert result.passes == 0
    assert result.near_misses == 1
    assert result.cases[0].verdict == "near_miss"
    assert result.cases[0].line_hit is False


def test_file_only_case_with_line_ranges_rejects_hit_outside_range(
    tmp_path, monkeypatch
):
    """primary_paths + primary_line_ranges, no symbols: hits outside the range must not pass."""
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "benchmark_version": "test-v1",
                "repo_name": "demo",
                "repo_commit": "abc123",
                "model_name": "fake-model",
                "top_k": 5,
                "line_tolerance": 5,
                "cases": [
                    {
                        "case_id": "range-only",
                        "query": "find target",
                        "primary_paths": ["src/Target.java"],
                        "primary_symbols": [],
                        "primary_line_ranges": [[80, 90]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    suite = load_eval_suite(fixture)
    service = _FakeSearchService(
        {
            "find target": SearchResponse(
                query="find target",
                repo_root="/tmp/repo",
                retrieval_version="v1",
                returned_count=2,
                has_more=False,
                results=(
                    SearchHit(
                        chunk_id="chunk-wrong",
                        kind="method",
                        symbol="com.app.Target.other",
                        file_path="src/Target.java",
                        start_line=10,
                        end_line=20,
                        source_set=None,
                        score=1.0,
                        confidence="high",
                        summary="wrong method, right file",
                        why_matched=("semantic",),
                    ),
                    SearchHit(
                        chunk_id="chunk-right",
                        kind="method",
                        symbol="com.app.Target.run",
                        file_path="src/Target.java",
                        start_line=82,
                        end_line=88,
                        source_set=None,
                        score=0.9,
                        confidence="high",
                        summary="correct range",
                        why_matched=("semantic",),
                    ),
                ),
            )
        }
    )
    monkeypatch.setattr(eval_module, "_current_git_commit", lambda: "deadbeef")
    monkeypatch.setattr(eval_module, "_is_git_dirty", lambda: False)

    result = run_eval_suite(service, suite, repo_root="/tmp/repo")

    # The first hit is in the right file but outside [80,90] — must NOT count.
    # The second hit falls inside the range — that should be the primary match at rank 2.
    assert result.strong_passes == 1
    assert result.passes == 0
    assert result.cases[0].verdict == "strong_pass"
    assert result.cases[0].primary_rank == 2
    assert result.cases[0].line_hit is True


def test_git_provenance_uses_codelens_repo_root(monkeypatch):
    calls = []

    def _fake_run(args, **kwargs):
        calls.append((args, kwargs))
        parts = tuple(str(part) for part in args)
        if "status" in parts and "--short" in parts:
            return SimpleNamespace(stdout=" M src/codelens/retrieval/eval.py\n")
        return SimpleNamespace(stdout="deadbeef\n")

    monkeypatch.setattr(eval_module.subprocess, "run", _fake_run)

    commit = eval_module._current_git_commit()
    dirty = eval_module._is_git_dirty()

    assert commit == "deadbeef"
    assert dirty is True
    assert calls[0][1]["cwd"] == eval_module.CODELENS_REPO_ROOT
    assert calls[1][1]["cwd"] == eval_module.CODELENS_REPO_ROOT
