# Video walkthrough outline

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
2. Follow the call into `PayloadService.create` in `service.py`. Explain the
   `BEGIN IMMEDIATE` writer lock and the race it prevents: two workers checking a
   missing string at the same time could otherwise both call the transformer.
3. Show `_transform_strings`: remove duplicate source strings, query cached results
   in batches, and transform only missing values. Show `transformer.py` and explain
   why a version is part of the cache key.
4. Show `Transformation` in `models.py` and the SQLite setup in `database.py`.
   Explain persistent storage, the composite primary key, and WAL mode.
5. Return to `create`: show the interleaving, output hash, and lookup of the payload
   identifier. Different original inputs can share the same ID if their output matches.
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
and the host uses `--host`.

Follow `read_input`, `run`, and `main`: read JSON, reuse the API schema, send a POST
and GET for each iteration, write JSON Lines, and return meaningful exit codes.
Explain the file, stdin, inline JSON, and output options.

Optional live command with the service running:

```bash
cache-cli --input examples/input.json --repeat 3
```

## 5. Tests and Docker (about 2 minutes)

Show tests for repeated requests and the transformer call count, overlapping strings,
restart persistence, and concurrency through two separate application engines.
Explain that the mock counts external-service calls while SQLite and files are real.
Show rollback and file replacement tests, then CLI tests with `MockTransport`.

```bash
pytest --cov=caching_service --cov-report=term-missing
```

Show the Dockerfile, non-root user, healthcheck, and Compose volume. Explain what
survives a container restart. Mention the CI checks and dependency constraints.

## 6. Tradeoffs (about 1 minute)

Be ready to explain these decisions without reading a script:

- Why SQLite is enough for the exercise, and what serialization costs.
- Why the transformer version belongs in the cache key.
- Why hashing the output deduplicates payloads more broadly than hashing input.
- Why database and filesystem updates are not one atomic transaction.
- Why successful transformations can be repeated if a request rolls back.
- What would change for a slow external transformer or a distributed deployment.

The submission also needs the actual time you spent. Record your real time rather
than copying the task's estimated 3-4 hours.
