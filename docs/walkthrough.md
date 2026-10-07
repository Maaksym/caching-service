# Video walkthrough outline

<!-- Це план відео. Шлях файлів/функцій коротко записано в request-flow-uk.md. -->

Aim for 8-12 minutes. Share your screen with the camera on. Explain the code in
your own words, and be ready to modify it during the interview.

## 1. Purpose and layout (about 1 minute)

Explain that the service accepts two equally sized lists, transforms their strings,
interleaves them, and persists the output. There are two caches: individual string
results and whole generated payloads. Show the source directory, tests, Dockerfile,
and CLI entry point in `pyproject.toml`.

## 2. Follow a POST request (about 3 minutes)

Start at `create_payload` in `api.py` and use `examples/input.json` as the example.

1. Show `PayloadInput` in `schemas.py`: two lists of strings, equal-length validation,
   and rejection of unexpected fields.
2. Follow `PayloadService.create` in `service.py`: remove duplicate source strings.
   Show `_cached_payload` and `_load_cached_strings`: read cached transformations
   without a writer lock, build the output/ID, and check the registry and JSON file.
   A complete cache hit returns immediately, even while another writer is busy.
3. For a cache miss, show `write_session` in `database.py`. It acquires SQLite's
   `BEGIN IMMEDIATE` writer lock. Back in `_transform_strings`, check the cache
   again before transforming: another worker may have filled it during the wait.
   Show `transformer.py` and why a version is part of the cache key.
4. Show `Transformation` in `models.py` and the SQLite setup in `database.py`.
   Explain persistent storage, the composite primary key, and WAL mode.
5. Show `_build_payload`: interleave strings and hash the output. Return to `create`
   for the registry lookup. Different inputs can share an ID if their output matches.
6. Show `PayloadStore.write` in `storage.py`: temporary file, flush, atomic rename,
   and the database commit afterward. Mention the crash window and orphan-file tradeoff.
7. Return to the endpoint: explain `201` for a new output, `200` for cache reuse,
   and the `Location` header.

## 3. Follow GET (about 1 minute)

Start at `read_payload`. Explain identifier validation, the database registry lookup,
`404` for a missing record, the JSON file read, and content-hash verification.
Show the `503` storage error handler. Explain how repeating POST can restore a
missing or corrupt file without calling the transformer again.

## 4. CLI (about 2 minutes)

Show `CliSettings`: Pydantic Settings handles parsing, URL validation, positive
repeat counts, and mutually exclusive input sources. Explain why `-h` means help
and the host uses `--host`. `env_prefix_target="all"` ensures the alias `--json`
uses `CACHE_CLI_JSON`; the unrelated environment variable `JSON` is ignored.

Follow `read_input`, `run`, and `main`: read JSON, reuse the API schema, send a POST
and GET for each iteration, write JSON Lines, and return meaningful exit codes.
Explain the file, stdin, inline JSON, and output options.
Show `read_stdin_utf8` and `write_utf8`: read/write UTF-8 bytes directly so Windows
cp1251/cp1252 text wrappers cannot corrupt Unicode. The pipe producer must send
UTF-8; `--output` avoids any subsequent re-encoding by the shell.

Optional live command with the service running:

```bash
cache-cli --input examples/input.json --repeat 3
```

## 5. Tests and Docker (about 2 minutes)

Show tests for repeated requests and the transformer call count, overlapping strings,
restart persistence, and concurrency through two separate application engines.
Explain that the mock counts external-service calls while SQLite and files are real.
Show rollback and file replacement tests, then CLI tests with `MockTransport`.
Show `test_cached_payload_does_not_wait_for_slow_writer`: events keep one writer
inside the transformer while a cached request completes through a second engine.
For CLI encoding, show the `BytesIO`/`TextIOWrapper` tests that inspect actual bytes,
plus the tests for prefixed and unrelated JSON environment variables.

```bash
pytest --cov=caching_service --cov-report=term-missing
```

Show the Dockerfile, non-root user, healthcheck, and Compose volume. Explain what
survives a container restart. Mention the CI checks and dependency constraints.

## 6. Tradeoffs (about 1 minute)

Be ready to explain these decisions without reading a script:

- Why SQLite satisfies the choice of database in the task. The database module is
  SQLite-specific; moving its lock helper does not add PostgreSQL support.
- Why cache hits avoid the writer lock, while new/repair requests still serialize.
- Why the transformer version belongs in the cache key.
- Why hashing the output deduplicates payloads more broadly than hashing input.
- Why database and filesystem updates are not one atomic transaction.
- Why successful transformations can be repeated if a request rolls back.
- What would change for a slow external transformer or a distributed deployment.

The submission also needs the actual time you spent. Record your real time rather
than copying the task's estimated 3-4 hours.
