# Caching Service

A Python backend exercise: a FastAPI service that transforms strings, caches
transformation results, and reuses identifiers for previously generated payloads.

## Planned behavior

- `POST /payload` accepts two equally sized lists of strings and returns a payload ID.
- The transformer converts each string to uppercase.
- Transformed strings from the two lists are interleaved and joined with `, `.
- `GET /payload/{id}` returns the generated output.
- Transformation results and generated payloads persist in SQLite via SQLAlchemy.
- A CLI uses Pydantic Settings to validate arguments and exercise the service.
- Automated tests cover the API, cache reuse, and CLI behavior.
- Docker provides a reproducible way to run the application.

## Status

Repository initialized. Implementation and usage instructions will be added
incrementally.

## Decisions to confirm

- The task assigns `-h` to both host and help. Reserve `-h` for help and use `--host`
  for the server URL.
- Confirm whether generated payloads must be physical files or may be stored in
  the database. The task mentions files but shows JSON API responses.
