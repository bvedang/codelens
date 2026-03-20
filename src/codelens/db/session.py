from contextlib import contextmanager

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from codelens.db import schema as _schema  # noqa: F401
from codelens.db.constants import DB_URL, PRAGMA_FOREIGN_KEYS, PRAGMA_WAL_MODE

engine = create_engine(DB_URL)

@event.listens_for(engine, "connect")
def _set_sqlite_params(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute(PRAGMA_WAL_MODE)
    cursor.execute(PRAGMA_FOREIGN_KEYS)
    cursor.close()


def init_db():
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session
