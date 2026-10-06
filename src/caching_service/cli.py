from __future__ import annotations

import json
import sys
from contextlib import ExitStack
from pathlib import Path

import httpx
from pydantic import Field, HttpUrl, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError

from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput


class CliSettings(BaseSettings):
    """Create and read cached payloads; emit one JSON line per iteration."""

    model_config = SettingsConfigDict(
        env_prefix="CACHE_CLI_",
        cli_prog_name="cache-cli",
        cli_exit_on_error=False,
        cli_hide_none_type=True,
        cli_shortcuts={"repeat": "r", "input": "i", "json": "j", "output": "o"},
    )

    host: HttpUrl = Field(default="http://127.0.0.1:8000", description="Server base URL")
    repeat: int = Field(default=1, gt=0, description="Number of POST/GET iterations")
    input: str | None = Field(default=None, description="Input JSON file, or - for stdin (default)")
    json_input: str | None = Field(default=None, alias="json", description="Inline JSON input")
    output: str = Field(default="-", description="Output file, or - for stdout")

    @model_validator(mode="after")
    def one_input_source(self) -> CliSettings:
        if self.input is not None and self.json_input is not None:
            raise ValueError("--input and --json cannot be used together")
        return self

    @field_validator("host")
    @classmethod
    def base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.query or value.fragment or value.username or value.password:
            raise ValueError("--host must not contain credentials, a query, or a fragment")
        return value


def read_input(settings: CliSettings) -> PayloadInput:
    if settings.json_input is not None:
        raw = settings.json_input
    elif settings.input is None or settings.input == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(settings.input).read_text(encoding="utf-8")
    return PayloadInput.model_validate_json(raw)


def run(settings: CliSettings) -> None:
    # Consume input before opening output, so the same path can safely serve both roles.
    payload = read_input(settings)
    with ExitStack() as stack:
        output = (
            sys.stdout
            if settings.output == "-"
            else stack.enter_context(Path(settings.output).open("w", encoding="utf-8"))
        )
        client = stack.enter_context(
            httpx.Client(base_url=str(settings.host).rstrip("/") + "/", timeout=30)
        )
        for iteration in range(1, settings.repeat + 1):
            response = client.post("payload", json=payload.model_dump())
            response.raise_for_status()
            created = PayloadCreated.model_validate_json(response.content)
            response = client.get(f"payload/{created.id}")
            response.raise_for_status()
            result = PayloadOutput.model_validate_json(response.content)
            output.write(
                json.dumps(
                    {"iteration": iteration, **created.model_dump(), **result.model_dump()},
                    ensure_ascii=False,
                )
                + "\n"
            )


def main(argv: list[str] | None = None) -> int:
    try:
        settings = CliSettings(_cli_parse_args=sys.argv[1:] if argv is None else argv)
        run(settings)
    except (SettingsError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(f"error: HTTP {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1
    except (httpx.RequestError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
