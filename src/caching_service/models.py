# ORM-моделі описують SQLite-таблиці; HTTP-дані описано окремо в schemas.py.
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Спільна основа: database.py бере опис таблиць із Base.metadata.
    pass


class Transformation(Base):
    # Кеш одного рядка: (версія, оригінал) -> перетворений текст.
    __tablename__ = "transformations"

    # Два primary_key утворюють один складений ключ: одна пара -> один запис.
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    result: Mapped[str] = mapped_column(Text)


class Payload(Base):
    # Реєстр готових результатів; сам output лежить у JSON-файлі, а не в цій таблиці.
    __tablename__ = "payloads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Функція default формує час UTC при вставці нового запису.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
