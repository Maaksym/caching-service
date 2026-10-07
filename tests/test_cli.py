# Перевірки CLI; MockTransport підміняє мережу, решта клієнтського коду працює.
import io
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from caching_service.cli import CliSettings, main

INPUT = {"list_1": ["hello"], "list_2": ["world"]}
PAYLOAD_ID = "a" * 64
UNICODE_INPUT = {"list_1": ["привіт"], "list_2": ["світ"]}
UNICODE_OUTPUT = "ПРИВІТ, СВІТ"


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    # Збираємо HTTP-запити й підставляємо відповіді через MockTransport.
    requests: list[httpx.Request] = []
    client_type = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == INPUT
            cached = len(requests) > 1
            return httpx.Response(200 if cached else 201, json={"id": PAYLOAD_ID, "cached": cached})
        assert request.url.path.endswith(f"/payload/{PAYLOAD_ID}")
        return httpx.Response(200, json={"output": "HELLO, WORLD"})

    def make_client(**kwargs) -> httpx.Client:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr("caching_service.cli.httpx.Client", make_client)
    return requests


def test_inline_json_repeat_and_aliases(requests: list[httpx.Request], capsys) -> None:
    # Inline JSON і короткі прапорці: три ітерації -> шість HTTP-запитів.
    assert main(["-j", json.dumps(INPUT), "-r", "3", "--host", "http://localhost:9000"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(requests) == 6
    assert {request.url.port for request in requests} == {9000}
    assert rows == [
        {"iteration": i, "id": PAYLOAD_ID, "cached": i > 1, "output": "HELLO, WORLD"}
        for i in range(1, 4)
    ]


def test_input_and_output_files(tmp_path: Path, requests: list[httpx.Request], capsys) -> None:
    # CLI читає JSON із файлу й пише результат у файл замість stdout.
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "result.jsonl"
    input_path.write_text(json.dumps(INPUT), encoding="utf-8")
    assert main(["-i", str(input_path), "-o", str(output_path)]) == 0
    assert len(requests) == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["output"] == "HELLO, WORLD"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("arguments", [[], ["-i", "-", "-o", "-"]])
def test_stdin_and_stdout(
    requests: list[httpx.Request], monkeypatch: pytest.MonkeyPatch, capsys, arguments: list[str]
) -> None:
    # StringIO імітує stdin; capsys збирає надрукований результат.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(INPUT)))
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["id"] == PAYLOAD_ID
    assert len(requests) == 2


def test_output_may_replace_input(tmp_path: Path, requests: list[httpx.Request]) -> None:
    # Input читається до відкриття output, тому однаковий шлях безпечний.
    path = tmp_path / "input.json"
    path.write_text(json.dumps(INPUT), encoding="utf-8")
    assert main(["-i", str(path), "-o", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["output"] == "HELLO, WORLD"


def test_host_path_prefix_is_preserved(requests: list[httpx.Request]) -> None:
    # Відносні URL зберігають префікс /api/v1 у базовій адресі.
    assert main(["--json", json.dumps(INPUT), "--host", "http://localhost:9000/api/v1"]) == 0
    assert requests[0].url.path == "/api/v1/payload"
    assert requests[1].url.path == f"/api/v1/payload/{PAYLOAD_ID}"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_exits_successfully(flag: str, capsys) -> None:
    # Довідка -h/--help завершується успішним кодом 0.
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert "--host" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--repeat", "0"],
        ["--repeat", "-1"],
        ["--repeat", "abc"],
        ["--host", "ftp://localhost"],
        ["--host", "http://localhost?query=x"],
        ["--host", "http://user:password@localhost"],
        ["--json", "{}", "--input", "-"],
        ["--json", "not json"],
        ["--json", '{"list_1": [1], "list_2": ["a"]}'],
        ["--unknown-option", "value"],
    ],
)
def test_invalid_arguments_fail_before_http(
    arguments: list[str], requests: list[httpx.Request], capsys
) -> None:
    # Неправильні аргументи або JSON дають код 2 без HTTP-запитів.
    assert main(arguments) == 2
    assert "error:" in capsys.readouterr().err
    assert requests == []


def test_missing_input_file_returns_error(tmp_path: Path, capsys) -> None:
    # Відсутній локальний файл дає stderr і код 1.
    assert main(["--input", str(tmp_path / "missing.json")]) == 1
    assert "error:" in capsys.readouterr().err


def test_http_failure_returns_error(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    # HTTP 503 від сервера дає повідомлення зі статусом і код 1.
    client_type = httpx.Client

    def make_client(**kwargs) -> httpx.Client:
        return client_type(
            transport=httpx.MockTransport(lambda request: httpx.Response(503, text="unavailable")),
            **kwargs,
        )

    monkeypatch.setattr("caching_service.cli.httpx.Client", make_client)
    assert main(["--json", json.dumps(INPUT)]) == 1
    assert "HTTP 503" in capsys.readouterr().err


def test_connection_failure_returns_error(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    # Неможливість підключення дає зрозуміле повідомлення і код 1.
    def make_client(**kwargs) -> httpx.Client:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("caching_service.cli.httpx.Client", make_client)
    assert main(["--json", json.dumps(INPUT)]) == 1
    assert "connection refused" in capsys.readouterr().err


def test_repeat_is_validated_by_pydantic_settings() -> None:
    # Навіть пряме створення settings відхиляє repeat=0.
    with pytest.raises(ValidationError):
        CliSettings(repeat=0)


@pytest.fixture
def unicode_requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    # Транспорт перевіряє справжні Unicode-дані запиту і повертає відому відповідь.
    requests: list[httpx.Request] = []
    client_type = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert json.loads(request.content) == UNICODE_INPUT
            return httpx.Response(201, json={"id": PAYLOAD_ID, "cached": False})
        return httpx.Response(200, json={"output": UNICODE_OUTPUT})

    def make_client(**kwargs) -> httpx.Client:
        return client_type(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr("caching_service.cli.httpx.Client", make_client)
    return requests


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252"])
@pytest.mark.parametrize("arguments", [[], ["--input", "-"]])
def test_stdin_utf8_bytes_ignore_windows_text_encoding(
    encoding: str, arguments: list[str], unicode_requests, monkeypatch, capsys
) -> None:
    # TextIOWrapper має Windows-кодування, але байти в pipe є UTF-8.
    data = json.dumps(UNICODE_INPUT, ensure_ascii=False).encode("utf-8")
    stdin = io.TextIOWrapper(io.BytesIO(data), encoding=encoding)
    monkeypatch.setattr("sys.stdin", stdin)
    assert main(arguments) == 0
    assert len(unicode_requests) == 2
    assert json.loads(capsys.readouterr().out)["output"] == UNICODE_OUTPUT


@pytest.mark.parametrize("encoding", ["cp1251", "cp1252"])
def test_stdout_is_utf8_bytes_regardless_of_windows_text_encoding(
    encoding: str, unicode_requests, monkeypatch
) -> None:
    # Перевіряємо саме байти перенаправленого stdout, а не лише текст capsys.
    output = io.BytesIO()
    stdout = io.TextIOWrapper(output, encoding=encoding, write_through=True)
    monkeypatch.setattr("sys.stdout", stdout)
    assert main(["--json", json.dumps(UNICODE_INPUT, ensure_ascii=False)]) == 0
    assert len(unicode_requests) == 2
    assert json.loads(output.getvalue().decode("utf-8"))["output"] == UNICODE_OUTPUT


def test_invalid_utf8_stdin_fails_before_http(requests, monkeypatch, capsys) -> None:
    # Байти іншого кодування не мають тихо перетворюватися на сміття в кеші.
    data = b'{"list_1": ["\xff"], "list_2": ["world"]}'
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1251"))
    assert main(["--input", "-"]) == 1
    assert requests == []
    assert "error:" in capsys.readouterr().err


def test_unprefixed_json_environment_cannot_inject_input(monkeypatch) -> None:
    # Звичайна системна змінна JSON не є налаштуванням нашого CLI.
    monkeypatch.setenv("JSON", json.dumps(INPUT))
    monkeypatch.delenv("CACHE_CLI_JSON", raising=False)
    settings = CliSettings(_cli_parse_args=["--input", "input.json"])
    assert settings.input == "input.json"
    assert settings.json_input is None


def test_prefixed_json_environment_is_supported(monkeypatch) -> None:
    # Alias --json також читає лише префіксовану змінну середовища.
    monkeypatch.setenv("CACHE_CLI_JSON", json.dumps(INPUT))
    monkeypatch.delenv("JSON", raising=False)
    assert CliSettings(_cli_parse_args=[]).json_input == json.dumps(INPUT)
