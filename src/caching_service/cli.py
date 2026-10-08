# Окремий клієнт: main() -> read_input() -> POST -> GET -> друк/файл.
# Він спілкується із сервером через HTTP і сам не читає серверну SQLite.
from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import TextIO

import httpx
from pydantic import Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput


class CliSettings(BaseSettings):
    """Create and read cached payloads; emit one JSON line per iteration."""

    # Параметри термінала/середовища й короткі форми -r, -i, -j, -o.
    model_config = SettingsConfigDict(
        env_prefix="CACHE_CLI_",
        # Префікс застосовується також до alias: --json читає CACHE_CLI_JSON, не JSON.
        env_prefix_target="all",
        cli_prog_name="cache-cli",
        cli_exit_on_error=False,
        cli_hide_none_type=True,
        cli_shortcuts={"repeat": "r", "input": "i", "json": "j", "output": "o"},
    )

    # host — адреса; repeat — кількість POST/GET; input/json — джерело; output — куди писати.
    host: HttpUrl = Field(default="http://127.0.0.1:8000", description="Server base URL")
    repeat: int = Field(default=1, gt=0, description="Number of POST/GET iterations")
    input: str | None = Field(default=None, description="Input JSON file, or - for stdin (default)")
    json_input: str | None = Field(default=None, alias="json", description="Inline JSON input")
    output: str = Field(default="-", description="Output file, or - for stdout")

    @model_validator(mode="after")
    def one_input_source(self) -> CliSettings:
        # Джерело даних одне: файл/stdin або inline JSON.
        if self.input is not None and self.json_input is not None:
            raise ValueError("--input and --json cannot be used together")
        return self

    @field_validator("host")
    @classmethod
    def base_url(cls, value: HttpUrl) -> HttpUrl:
        # Адреса сервера не повинна містити пароль, query або fragment.
        if value.query or value.fragment or value.username or value.password:
            raise ValueError("--host must not contain credentials, a query, or a fragment")
        return value


# Read UTF-8 data from standard input.
def read_stdin_utf8() -> str:
    # Реальний stdin має байтовий buffer: не декодуємо pipe через Windows cp1251/cp1252.
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8")
    # StringIO та інші вже текстові потоки не мають buffer.
    return sys.stdin.read()


# Write UTF-8 text to the selected output.
def write_utf8(stream: TextIO, value: str) -> None:
    # stdout/stderr і перенаправлення отримують саме UTF-8 незалежно від Windows locale.
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(value.encode("utf-8"))
        buffer.flush()
    else:
        # Підтримуємо текстові потоки без buffer, зокрема capsys/StringIO у тестах.
        stream.write(value)


# Read and validate the CLI input.
def read_input(settings: CliSettings) -> PayloadInput:
    # --json -> текст аргументу; --input файл -> його вміст; "-" або None -> stdin.
    if settings.json_input is not None:
        raw = settings.json_input
    elif settings.input is None or settings.input == "-":
        raw = read_stdin_utf8()
    else:
        raw = Path(settings.input).read_text(encoding="utf-8")
    # Перевіряємо дані до HTTP; передаємо серверу JSON-вміст, не сам файл.
    return PayloadInput.model_validate_json(raw)


# Send POST and GET requests to the API.
def run(settings: CliSettings) -> None:
    # Читаємо input до відкриття output, щоб однаковий шлях не стер вхідні дані.
    payload = read_input(settings)
    with ExitStack() as stack:
        # "-" означає екран; ExitStack закриє відкритий файл та HTTP-клієнт.
        output = (
            sys.stdout
            if settings.output == "-"
            else stack.enter_context(Path(settings.output).open("w", encoding="utf-8"))
        )
        client = stack.enter_context(
            httpx.Client(base_url=str(settings.host).rstrip("/") + "/", timeout=30)
        )
        for iteration in range(1, settings.repeat + 1):
            # 1. Створюємо/повторно використовуємо результат на сервері й отримуємо ID.
            response = client.post("payload", json=payload.model_dump())
            response.raise_for_status()
            created = PayloadCreated.model_validate_json(response.content)
            # 2. За отриманим ID читаємо output із сервера.
            response = client.get(f"payload/{created.id}")
            response.raise_for_status()
            result = PayloadOutput.model_validate_json(response.content)
            # 3. Один рядок JSON на ітерацію: iteration, id, cached, output.
            write_utf8(
                output,
                json.dumps(
                    {"iteration": iteration, **created.model_dump(), **result.model_dump()},
                    ensure_ascii=False,
                )
                + "\n",
            )


# Parse CLI arguments and start the client.
def main(argv: list[str] | None = None) -> int:
    # Вхід команди cache-cli: розбираємо параметри, запускаємо run(), повертаємо код.
    try:
        settings = CliSettings(_cli_parse_args=sys.argv[1:] if argv is None else argv)
        run(settings)
    except (SettingsError, ValidationError) as exc:
        # Код 2 — неправильні параметри/дані; діагностика окремо від output у stderr.
        write_utf8(sys.stderr, f"error: {exc}\n")
        return 2
    except httpx.HTTPStatusError as exc:
        # Код 1 — сервер відповів помилкою HTTP.
        write_utf8(sys.stderr, f"error: HTTP {exc.response.status_code}: {exc.response.text}\n")
        return 1
    except (httpx.RequestError, OSError, UnicodeError) as exc:
        # Код 1 — мережа, локальний файл або кодування.
        write_utf8(sys.stderr, f"error: {exc}\n")
        return 1
    return 0  # Код 0 означає успішне завершення.


if __name__ == "__main__":
    # Для запуску python -m caching_service.cli; при імпорті цей блок не виконується.
    raise SystemExit(main())
