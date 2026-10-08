# SQLite setup and safe write transaction helpers.
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session

from caching_service.models import Base


# Create the SQLite database connection and tables.
def create_database(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create the SQLite database connection.
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path.resolve())),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as connection:
        # WAL allows read requests to continue while another request is writing.
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        # Create the database tables if they do not exist yet.
        Base.metadata.create_all(connection)
    return engine


# Open a safe SQLite write transaction.
@contextmanager
def write_session(engine: Engine) -> Iterator[Session]:
    # Open a protected SQLite write transaction.
    if engine.dialect.name != "sqlite":
        raise ValueError("This database module supports SQLite only")
    with Session(engine) as session, session.begin():
        # Take the write lock before creating new cache entries.
        session.execute(text("BEGIN IMMEDIATE"))
        # Commit on success and roll back automatically if an error happens.
        yield session
