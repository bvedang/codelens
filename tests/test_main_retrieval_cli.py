from types import SimpleNamespace

import pytest

import codelens.__main__ as main_module


class _FakeEncoder:
    def __init__(self, *, device=None):
        self.device = device
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def test_run_retrieval_search_cli_prints_results(monkeypatch, capsys):
    encoder = _FakeEncoder(device="cpu")
    init_db_calls = []

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db_calls.append(True))
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())
    monkeypatch.setattr(main_module, "ColbertEncoder", lambda device=None: encoder)

    class _FakeService:
        def __init__(self, repository, encoder_arg):
            assert encoder_arg is encoder

        def search_code(self, query, **kwargs):
            assert query == "refund payment cancel"
            assert kwargs["top_k"] == 2
            return SimpleNamespace(
                to_dict=lambda: {
                    "query": query,
                    "returned_count": 1,
                    "has_more": False,
                    "results": [
                        {
                            "chunk_id": "chunk-1",
                            "kind": "method",
                            "symbol": "com.app.orders.OrderService.cancelOrder",
                            "file_path": "src/OrderService.java",
                            "start_line": 40,
                            "end_line": 52,
                            "score": 1.23,
                            "confidence": "high",
                            "summary": "Cancels an order and refunds payment.",
                            "why_matched": ["semantic", "terms:cancel, payment"],
                        }
                    ],
                }
            )

    monkeypatch.setattr(main_module, "RetrievalSearchService", _FakeService)

    main_module._run_retrieval_cli(
        [
            "search",
            "--repo-root",
            "/tmp/repo",
            "--query",
            "refund payment cancel",
            "--top-k",
            "2",
            "--device",
            "cpu",
        ]
    )

    output = capsys.readouterr().out
    assert "returned_count: 1" in output
    assert "cancelOrder" in output
    assert "lines      : 40-52" in output
    assert init_db_calls == [True]
    assert encoder.close_calls == 1


def test_run_retrieval_read_cli_prints_chunk(monkeypatch, capsys):
    init_db_calls = []

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db_calls.append(True))
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())

    class _FakeService:
        def __init__(self, repository, encoder_arg):
            assert isinstance(encoder_arg, main_module._NoopQueryEncoder)

        def read_code(self, chunk_id, *, include_surrounding):
            assert chunk_id == "chunk-1"
            assert include_surrounding is True
            return SimpleNamespace(
                to_dict=lambda: {
                    "chunk_id": chunk_id,
                    "kind": "method",
                    "symbol": "com.app.orders.OrderService.cancelOrder",
                    "file_path": "src/OrderService.java",
                    "start_line": 40,
                    "end_line": 52,
                    "summary": "Cancels an order and refunds payment.",
                    "source_text": "public void cancelOrder(Long orderId) { ... }",
                    "neighbors": [
                        {
                            "chunk_id": "chunk-2",
                            "kind": "method",
                            "symbol": "com.app.orders.OrderService.findOrder",
                            "start_line": 20,
                            "end_line": 28,
                        }
                    ],
                }
            )

    monkeypatch.setattr(main_module, "RetrievalSearchService", _FakeService)

    main_module._run_retrieval_cli(
        [
            "read",
            "--repo-root",
            "/tmp/repo",
            "--chunk-id",
            "chunk-1",
        ]
    )

    output = capsys.readouterr().out
    assert "chunk_id   : chunk-1" in output
    assert "source_text:" in output
    assert "findOrder" in output
    assert init_db_calls == [True]


def test_run_retrieval_read_cli_exits_when_chunk_missing(monkeypatch):
    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())

    class _FakeService:
        def __init__(self, repository, encoder_arg):
            pass

        def read_code(self, chunk_id, *, include_surrounding):
            return None

    monkeypatch.setattr(main_module, "RetrievalSearchService", _FakeService)

    with pytest.raises(SystemExit, match="Chunk not found: chunk-404"):
        main_module._run_retrieval_cli(
            [
                "read",
                "--repo-root",
                "/tmp/repo",
                "--chunk-id",
                "chunk-404",
            ]
        )


def test_run_retrieval_eval_cli_prints_summary(monkeypatch, capsys, tmp_path):
    encoder = _FakeEncoder(device="cpu")
    init_db_calls = []
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db_calls.append(True))
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())
    monkeypatch.setattr(main_module, "ColbertEncoder", lambda device=None: encoder)

    class _FakeService:
        def __init__(self, repository, encoder_arg):
            assert encoder_arg is encoder

    monkeypatch.setattr(main_module, "RetrievalSearchService", _FakeService)
    monkeypatch.setattr(main_module, "load_eval_suite", lambda path: {"fixture": path})
    monkeypatch.setattr(
        main_module,
        "run_eval_suite",
        lambda service, suite, *, repo_root, top_k=None: SimpleNamespace(
            to_dict=lambda: {
                "benchmark_version": "test-v1",
                "repo_name": "demo",
                "repo_commit": "abc123",
                "codelens_git_commit": "deadbeef",
                "codelens_git_dirty": False,
                "model_name": "fake-model",
                "retrieval_version": "v1",
                "ranking_config_version": "heuristic-rerank-v1",
                "top_k": 5,
                "strong_passes": 1,
                "passes": 0,
                "near_misses": 1,
                "fails": 0,
                "cases": [
                    {
                        "case_id": "primary",
                        "query": "find primary",
                        "verdict": "strong_pass",
                        "primary_rank": 1,
                        "matched_file": "src/Target.java",
                        "matched_symbol": "com.app.Target.run",
                        "line_hit": True,
                    }
                ],
            }
        ),
    )

    main_module._run_retrieval_cli(
        [
            "eval",
            "--repo-root",
            "/tmp/repo",
            "--fixture",
            str(fixture_path),
            "--device",
            "cpu",
        ]
    )

    output = capsys.readouterr().out
    assert "benchmark_version: test-v1" in output
    assert "codelens_commit   : deadbeef" in output
    assert "codelens_dirty    : False" in output
    assert f"fixture_path      : {fixture_path.resolve()}" in output
    assert "fixture_sha256    :" in output
    assert "retrieval_version : v1" in output
    assert "line_tolerance    : 25" in output
    assert "strong_passes     : 1" in output
    assert "[strong_pass] primary" in output
    assert init_db_calls == [True]
    assert encoder.close_calls == 1


def test_run_retrieval_eval_cli_writes_output_file(monkeypatch, tmp_path):
    encoder = _FakeEncoder(device="cpu")
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text("{}", encoding="utf-8")
    output_path = tmp_path / "results" / "run.json"

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: None)
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())
    monkeypatch.setattr(main_module, "ColbertEncoder", lambda device=None: encoder)

    class _FakeService:
        def __init__(self, repository, encoder_arg):
            assert encoder_arg is encoder

    monkeypatch.setattr(main_module, "RetrievalSearchService", _FakeService)
    monkeypatch.setattr(main_module, "load_eval_suite", lambda path: {"fixture": path})
    monkeypatch.setattr(
        main_module,
        "run_eval_suite",
        lambda service, suite, *, repo_root, top_k=None: SimpleNamespace(
            to_dict=lambda: {
                "benchmark_version": "test-v1",
                "repo_name": "demo",
                "repo_commit": "abc123",
                "codelens_git_commit": "deadbeef",
                "codelens_git_dirty": False,
                "model_name": "fake-model",
                "retrieval_version": "v1",
                "ranking_config_version": "heuristic-rerank-v1",
                "top_k": 5,
                "strong_passes": 1,
                "passes": 0,
                "near_misses": 0,
                "fails": 0,
                "cases": [],
            }
        ),
    )

    main_module._run_retrieval_cli(
        [
            "eval",
            "--repo-root",
            "/tmp/repo",
            "--fixture",
            str(fixture_path),
            "--device",
            "cpu",
            "--output",
            str(output_path),
        ]
    )

    assert output_path.exists()
    assert '"benchmark_version": "test-v1"' in output_path.read_text(encoding="utf-8")
    assert '"fixture_path":' in output_path.read_text(encoding="utf-8")
    assert encoder.close_calls == 1
