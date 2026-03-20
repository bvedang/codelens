import sqlite3

from sqlmodel import create_engine

import codelens.db.session as session_module


def test_init_db_creates_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "codelens.db"
    test_engine = create_engine(f"sqlite:///{db_path}")

    monkeypatch.setattr(session_module, "engine", test_engine)
    session_module.init_db()

    with session_module.get_session() as session:
        assert session is not None

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert "indexmeta" in tables
    assert "indexchunk" in tables
    assert "indexbuildstate" in tables
    assert "retrieval_documents" in tables
