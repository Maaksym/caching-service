from pathlib import Path

from sqlalchemy import URL, Engine, create_engine

from caching_service.models import Base


def create_database(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path.resolve())),
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as connection:
        # Readers can continue while a payload creation holds the SQLite writer lock.
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        Base.metadata.create_all(connection)
    return engine
