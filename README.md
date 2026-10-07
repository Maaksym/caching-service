# Caching Service

<!-- Орієнтир: цей файл пояснює встановлення й запуск, а не виконує код. -->
[Короткий шлях запиту українською](docs/request-flow-uk.md).

A FastAPI microservice that transforms two lists of strings, interleaves the
results, and persists both a transformation cache and generated JSON payload files.
A Pydantic Settings CLI exercises the API through real HTTP requests.

## Run locally

Requires Python 3.10 or newer. From the repository directory:

```bash
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

Then install and start the service:

```bash
python -m pip install --constraint requirements.lock -e ".[dev]"
python -m uvicorn caching_service.api:app --reload
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to try the endpoints.
For PyCharm, open this repository directory and select
`.venv/Scripts/python.exe` as the project interpreter on Windows.
Run the `uvicorn` module with parameters `caching_service.api:app --reload`
and the repository directory as its working directory.

If PowerShell prevents activation, use `.\.venv\Scripts\python.exe` and
`.\.venv\Scripts\cache-cli.exe` directly; activation is optional.

## Run with Docker

```bash
docker compose up --build
```

The API is available at `http://127.0.0.1:8000`. A named volume retains the
SQLite database and payload files across container replacements. The application
runs as a non-root user. `docker compose down` stops it without deleting its data.

The CLI is included in the image:

```bash
docker compose exec api cache-cli --input examples/input.json --repeat 3
```

## API

### Create a payload

`POST /payload` with `Content-Type: application/json`:

```json
{
  "list_1": ["first string", "second string", "third string"],
  "list_2": ["other string", "another string", "last string"]
}
```

Response:

```json
{"id": "<64-character SHA-256 digest>", "cached": false}
```

A new payload returns **201**. A previously generated output returns **200**
with `cached: true` and the same identifier. Both responses include a `Location`
header pointing to the read endpoint.

### Read a payload

`GET /payload/{id}` returns:

```json
{"output": "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"}
```

`GET /health` checks database connectivity and returns `{"status": "ok"}`.

Unequal list lengths, non-string elements, unknown fields, malformed JSON, and
invalid identifier formats return **422**. A valid but unknown identifier returns
**404**. Storage failures return **503**, with diagnostic details in server logs.
Empty lists are accepted and produce an empty output string.

## CLI

Run in a second terminal while the service is running:

```bash
cache-cli --host http://127.0.0.1:8000 --input examples/input.json --repeat 3
cache-cli -i examples/input.json -r 3 -o results.jsonl
```

Inline JSON, for shells that preserve single-quoted arguments:

```bash
cache-cli --json '{"list_1":["hello"],"list_2":["world"]}'
```

For Windows PowerShell, the file and stdin examples avoid native-command JSON
quoting differences:

```powershell
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $OutputEncoding
Get-Content examples/input.json -Raw -Encoding UTF8 | cache-cli -i -
```

```bash
# Linux / macOS
cat examples/input.json | cache-cli -i -
```

| Argument | Meaning | Default |
| --- | --- | --- |
| `--host URL` | HTTP(S) server base URL | `http://127.0.0.1:8000` |
| `-r`, `--repeat N` | Positive number of POST/GET iterations | `1` |
| `-i`, `--input FILE` | UTF-8 JSON file, or `-` for stdin | stdin |
| `-j`, `--json JSON` | Inline input; mutually exclusive with `--input` | unset |
| `-o`, `--output FILE` | JSON Lines file, or `-` for stdout | stdout |
| `-h`, `--help` | Usage information | |

Each iteration sends one POST, follows it with one GET, and emits one JSON line:

```json
{"iteration": 1, "id": "<digest>", "cached": false, "output": "HELLO, WORLD"}
{"iteration": 2, "id": "<same digest>", "cached": true, "output": "HELLO, WORLD"}
```

Exit codes are `0` for success, `2` for invalid arguments/input, and `1` for HTTP,
network, or file errors. Pydantic Settings performs argument parsing and validation;
the CLI uses the same payload schemas as the API.

Files and stdin input use UTF-8. JSON Lines and error messages are emitted as UTF-8
bytes, independently of the Windows locale. Invalid UTF-8 input fails before HTTP;
the program producing a pipe must also send UTF-8. Windows PowerShell can re-encode
native output when using `>`; use `--output results.jsonl` to guarantee a UTF-8 file.
See [PowerShell character encoding](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5).

Environment settings use the `CACHE_CLI_` prefix, including `CACHE_CLI_JSON` for
inline input. An unrelated variable named `JSON` is ignored. CLI arguments take
precedence over environment settings. This alias-prefix behavior requires
Pydantic Settings 2.15 or newer, as declared in `pyproject.toml`.

## Design

`api.py` validates HTTP requests and delegates to `PayloadService`. Synchronous
endpoints run blocking SQLAlchemy and file operations in FastAPI's thread pool.
The application lifespan initializes storage and disposes of the engine on shutdown.

There are two tables:

- `transformations`: `(version, source)` is the primary key; `result` stores the
  transformer outcome. Exact original strings are cache keys, preserving case and
  whitespace. Bumping `TRANSFORMER_VERSION` invalidates old transformation results.
- `payloads`: the SHA-256 digest of the final output is the primary key, with a
  creation timestamp. The payload itself lives in `data/payloads/{id}.json`.

On POST, the service deduplicates input strings and bulk-loads cached outcomes in
bounded batches without acquiring a writer lock. If all transformations, the output
registry entry, and a valid payload file exist, it returns the cached ID immediately.
WAL mode lets this read succeed while another request transforms new strings.

Otherwise, `database.write_session` acquires SQLite's writer lock using
`BEGIN IMMEDIATE`. The service checks transformations again after acquiring the lock:
another worker might have populated them during the wait. It calls the transformer
only for values still missing, interleaves the results, and hashes the final output.
Concurrent writers therefore cannot both process the same uncached string in
successful requests. Identical outputs reuse an identifier even when their original
inputs differ in case. Connections wait up to 30 seconds for the writer lock.

A new payload is written to a temporary file in its destination directory, flushed,
and atomically renamed before its database record is committed. GET first checks
the record, then reads the file and verifies that its content matches the identifier.
Repeating POST repairs a missing or corrupt file using cached transformations.

Set `CACHE_DATA_DIR` to change the storage directory (default: `./data`):

```powershell
$env:CACHE_DATA_DIR = 'C:\path\to\data'
```

```bash
export CACHE_DATA_DIR=/path/to/data
```

## Assumptions and tradeoffs

- The sample implies uppercase conversion, so the simulated transformer uses
  `str.upper()` and is assumed deterministic for a given version.
- The wording mentions payload files, so the output is an actual JSON file; the
  transformation cache and payload registry live in SQLite.
- The task assigns `-h` to both host and help. This implementation reserves `-h`
  for help and exposes the server through `--host`.
- Both storage resources must be retained together. Multiple workers may share
  the same local database and payload directory; this is not a distributed cache.
- The task allows SQLite or PostgreSQL; this implementation chooses SQLite.
  Database-specific locking lives in `database.py`. PostgreSQL would require its
  own configuration and concurrency strategy; it is not supported by this module.
- Fully cached POSTs do not acquire the writer lock. Requests creating or repairing
  data still serialize, including transformer calls, and can time out with 503.
  A slow real transformer would require a different coordination strategy for
  higher write throughput.
- A database transaction and a filesystem write cannot form one atomic commit.
  A crash after file publication but before database commit can leave an unreferenced
  file; a retry replaces it safely. Database rollback can also cause successful
  transformations in a failed request to be repeated. There is no exactly-once
  guarantee across external-service failures or process crashes.
- Tables are created on startup for the exercise. Production schema changes would
  use migrations. Cache expiry, eviction, authentication, and request-size quotas
  are outside this task's scope.

## Checks

```bash
pytest --cov=caching_service --cov-report=term-missing
ruff check .
ruff format --check .
```

Tests cover the sample output, duplicate strings, overlapping requests, output
deduplication, ordering, validation, Unicode, empty inputs, persistence after restart,
concurrent app instances, rollback, atomic file replacement, file recovery, and CLI
input/output/error handling. Tests use temporary SQLite files and payload directories.
CLI tests use HTTPX's mock transport and verify both POST and GET requests.
Byte-stream tests emulate cp1251/cp1252 wrappers to check UTF-8 stdin/stdout rather
than just decoded text. Regression tests also check environment-prefix isolation
and a fully cached POST completing while another engine holds the writer lock.

`requirements.lock` pins the tested runtime and development dependencies; `pyproject.toml`
declares the compatible ranges. GitHub Actions checks Python 3.10 and 3.12 and builds
the Docker image.

An English video walkthrough outline is in [docs/walkthrough.md](docs/walkthrough.md).
