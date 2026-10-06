import hashlib
from collections.abc import Callable
from itertools import chain

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from caching_service.models import Payload, Transformation
from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput
from caching_service.storage import PayloadStorageError, PayloadStore
from caching_service.transformer import TRANSFORMER_VERSION, transform


class PayloadService:
    def __init__(
        self,
        engine: Engine,
        store: PayloadStore,
        transformer: Callable[[str], str] = transform,
        transformer_version: str = TRANSFORMER_VERSION,
    ) -> None:
        self.engine = engine
        self.store = store
        self.transformer = transformer
        self.transformer_version = transformer_version

    def create(self, request: PayloadInput) -> PayloadCreated:
        with Session(self.engine) as session, session.begin():
            # Lock before checking the cache: separate workers must not transform the same miss.
            session.execute(text("BEGIN IMMEDIATE"))
            results = self._transform_strings(session, request)
            output = ", ".join(
                results[value]
                for pair in zip(request.list_1, request.list_2, strict=True)
                for value in pair
            )
            payload_id = hashlib.sha256(output.encode("utf-8")).hexdigest()
            cached = session.get(Payload, payload_id) is not None
            if not cached:
                self.store.write(payload_id, output)
                session.add(Payload(id=payload_id))
            else:
                try:
                    self.store.read(payload_id)
                except PayloadStorageError:
                    # Restore missing or corrupt files without repeating transformations.
                    self.store.write(payload_id, output)
            response = PayloadCreated(id=payload_id, cached=cached)
        return response

    def read(self, payload_id: str) -> PayloadOutput | None:
        with Session(self.engine) as session:
            if session.get(Payload, payload_id) is None:
                return None
        return self.store.read(payload_id)

    def _transform_strings(self, session: Session, request: PayloadInput) -> dict[str, str]:
        sources = list(dict.fromkeys(chain(request.list_1, request.list_2)))
        results: dict[str, str] = {}
        # Bound SQLite query parameters without imposing a limit on the input lists.
        for offset in range(0, len(sources), 500):
            rows = session.scalars(
                select(Transformation).where(
                    Transformation.version == self.transformer_version,
                    Transformation.source.in_(sources[offset : offset + 500]),
                )
            )
            results.update((row.source, row.result) for row in rows)
        for source in sources:
            if source not in results:
                result = self.transformer(source)
                results[source] = result
                session.add(
                    Transformation(version=self.transformer_version, source=source, result=result)
                )
        return results
