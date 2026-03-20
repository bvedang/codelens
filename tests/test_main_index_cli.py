from types import SimpleNamespace

import pytest

import codelens.__main__ as main_module


class _FakeEncoder:
    def __init__(self, *, device=None):
        self.device = device
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def test_run_index_cli_closes_encoder_on_success(monkeypatch, capsys):
    encoder = _FakeEncoder(device="cpu")
    events = []
    init_db_calls = []

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db_calls.append(True))
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())
    monkeypatch.setattr(main_module, "ColbertEncoder", lambda device=None: encoder)
    monkeypatch.setattr(
        main_module,
        "log_event",
        lambda logger, level, message, **fields: events.append((message, fields)),
    )

    class _FakeService:
        def __init__(self, repository, encoder_arg, *, batch_size):
            assert encoder_arg is encoder
            assert batch_size == 8

        def index_workspace(self, **kwargs):
            return SimpleNamespace(
                repo_root=str(kwargs["repo_root"]),
                files_indexed=1,
                documents_indexed=2,
                failures=0,
            )

    monkeypatch.setattr(main_module, "FaissIndexingService", _FakeService)

    main_module._run_index_cli(
        [
            "workspace",
            "--repo-root",
            "/tmp/repo",
            "--workspace-json",
            "/tmp/workspace.json",
            "--device",
            "cpu",
            "--batch-size",
            "8",
        ]
    )

    output = capsys.readouterr().out
    assert "files_indexed    : 1" in output
    assert init_db_calls == [True]
    assert encoder.close_calls == 1
    assert events[0][0] == "Starting index command"
    assert events[1][0] == "Index command completed"
    assert events[1][1]["files_indexed"] == 1


def test_run_index_cli_closes_encoder_on_failure(monkeypatch):
    encoder = _FakeEncoder(device="cpu")
    init_db_calls = []

    monkeypatch.setattr(main_module, "configure_logging", lambda verbose: None)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db_calls.append(True))
    monkeypatch.setattr(main_module, "FaissIndexRepository", lambda repo_root: object())
    monkeypatch.setattr(main_module, "ColbertEncoder", lambda device=None: encoder)

    class _FailingService:
        def __init__(self, repository, encoder_arg, *, batch_size):
            assert encoder_arg is encoder
            assert batch_size == 4

        def index_workspace(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "FaissIndexingService", _FailingService)

    with pytest.raises(RuntimeError, match="boom"):
        main_module._run_index_cli(
            [
                "workspace",
                "--repo-root",
                "/tmp/repo",
                "--workspace-json",
                "/tmp/workspace.json",
                "--batch-size",
                "4",
            ]
        )

    assert init_db_calls == [True]
    assert encoder.close_calls == 1
