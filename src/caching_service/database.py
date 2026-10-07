# Підготовка SQLite при запуску: api.py -> create_database() -> таблиці з models.py.
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session

from caching_service.models import Base


def create_database(path: Path) -> Engine:
    # Створюємо папку й engine, через який сервіс відкриватиме сесії.
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path.resolve())),
        # FastAPI працює з потоками; SQLite чекає блокування до 30 секунд.
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as connection:
        # WAL дозволяє читання, поки інший запит тримає блокування запису.
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        # Створює відсутні таблиці; не перебудовує старі таблиці як система міграцій.
        Base.metadata.create_all(connection)
    return engine


@contextmanager
def write_session(engine: Engine) -> Iterator[Session]:
    # Проєкт обрав SQLite; його спеціальний SQL ізольовано тут, а не в бізнес-логіці.
    if engine.dialect.name != "sqlite":
        raise ValueError("This database module supports SQLite only")
    with Session(engine) as session, session.begin():
        # Після оптимістичного читання сервіс повторить пошук уже під writer lock.
        session.execute(text("BEGIN IMMEDIATE"))
        # Успіх -> commit; помилка -> rollback; контекст також закриває сесію.
        yield session
