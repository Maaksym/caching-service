# Правила JSON для запитів, відповідей і файлів; таблиці бази описано в models.py.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class PayloadInput(BaseModel):
    # [POST 1] FastAPI перевіряє два списки строгих рядків і забороняє зайві поля.
    model_config = ConfigDict(extra="forbid")

    list_1: list[StrictStr]
    list_2: list[StrictStr]

    @model_validator(mode="after")
    def equal_lengths(self) -> PayloadInput:
        # Порівнюємо кількість елементів, не довжину слів; помилка HTTP-запиту дає 422.
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    # Відповідь POST: ID кінцевого результату і ознака, чи такий ID уже є в базі.
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cached: bool


class PayloadOutput(BaseModel):
    # Відповідь GET і формат збереженого JSON-файлу: {"output": "..."}.
    model_config = ConfigDict(extra="forbid")

    output: StrictStr
