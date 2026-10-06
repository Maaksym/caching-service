import io
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from caching_service.cli import CliSettings, main

INPUT = {"list_1": ["hello"], "list_2": ["world"]}
PAYLOAD_ID = "a" * 64


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
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
    assert main(["-j", json.dumps(INPUT), "-r", "3", "--host", "http://localhost:9000"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(requests) == 6
    assert {request.url.port for request in requests} == {9000}
    assert rows == [
        {"iteration": i, "id": PAYLOAD_ID, "cached": i > 1, "output": "HELLO, WORLD"}
        for i in range(1, 4)
    ]


def test_input_and_output_files(tmp_path: Path, requests: list[httpx.Request], capsys) -> None:
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
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(INPUT)))
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["id"] == PAYLOAD_ID
    assert len(requests) == 2


def test_output_may_replace_input(tmp_path: Path, requests: list[httpx.Request]) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(INPUT), encoding="utf-8")
    assert main(["-i", str(path), "-o", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["output"] == "HELLO, WORLD"


def test_host_path_prefix_is_preserved(requests: list[httpx.Request]) -> None:
    assert main(["--json", json.dumps(INPUT), "--host", "http://localhost:9000/api/v1"]) == 0
    assert requests[0].url.path == "/api/v1/payload"
    assert requests[1].url.path == f"/api/v1/payload/{PAYLOAD_ID}"


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_exits_successfully(flag: str, capsys) -> None:
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
    assert main(arguments) == 2
    assert "error:" in capsys.readouterr().err
    assert requests == []


def test_missing_input_file_returns_error(tmp_path: Path, capsys) -> None:
    assert main(["--input", str(tmp_path / "missing.json")]) == 1
    assert "error:" in capsys.readouterr().err


def test_http_failure_returns_error(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
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
    def make_client(**kwargs) -> httpx.Client:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("caching_service.cli.httpx.Client", make_client)
    assert main(["--json", json.dumps(INPUT)]) == 1
    assert "connection refused" in capsys.readouterr().err


def test_repeat_is_validated_by_pydantic_settings() -> None:
    with pytest.raises(ValidationError):
        CliSettings(repeat=0)
