# Main caching and payload generation logic.
import hashlib
from collections.abc import Callable
from itertools import chain

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from caching_service.database import write_session
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
        # Store the database, file storage, and transformer for later use.
        self.engine = engine
        self.store = store
        self.transformer = transformer
        self.transformer_version = transformer_version

    # Create a new payload or reuse an existing cached payload.
    def create(self, request: PayloadInput) -> PayloadCreated:
        # Combine both lists and remove duplicate strings.
        sources = list(dict.fromkeys(chain(request.list_1, request.list_2)))
        # First, try to return the complete payload from cache.
        cached_payload = self._cached_payload(request, sources)
        # If everything already exists, return it immediately.
        if cached_payload is not None:
            return cached_payload

        # If something is missing, open a protected database write transaction.
        with write_session(self.engine) as session:
            # Check the cache again and transform only missing strings.
            results = self._transform_strings(session, sources)
            # Build the final output and create its ID.
            output, payload_id = self._build_payload(request, results)
            # Check if this complete payload was already created before.
            cached = session.get(Payload, payload_id) is not None
            if not cached:
                # Save a new payload as a JSON file.
                self.store.write(payload_id, output)
                # Save the payload ID in the database.
                session.add(Payload(id=payload_id))
            else:
                try:
                    self.store.read(payload_id)
                except PayloadStorageError:
                    # Recreate the JSON file if it is missing or corrupted.
                    self.store.write(payload_id, output)
            response = PayloadCreated(id=payload_id, cached=cached)
        # Return the payload ID and cache status back to api.py.
        return response

    # Read an existing payload by its ID.
    def read(self, payload_id: str) -> PayloadOutput | None:
        # Check that the payload ID exists in the database.
        with Session(self.engine) as session:
            if session.get(Payload, payload_id) is None:
                return None
        # Read the saved JSON file. GET never calls the transformer.
        return self.store.read(payload_id)

    # Try to return a fully cached payload without writing to the database.
    def _cached_payload(self, request: PayloadInput, sources: list[str]) -> PayloadCreated | None:
        # Try to return a fully cached payload without taking a write lock.
        with Session(self.engine) as session:
            # Load already transformed strings from SQLite.
            results = self._load_cached_strings(session, sources)
            # If some strings are missing, the full payload is not cached.
            if len(results) != len(sources):
                return None
            output, payload_id = self._build_payload(request, results)
            # Check that the complete payload is registered in the database.
            if session.get(Payload, payload_id) is None:
                return None
            try:
                # Check that the payload JSON file also exists and is valid.
                self.store.read(payload_id)
            except PayloadStorageError:
                return None
            # Everything exists, so return the cached payload.
            return PayloadCreated(id=payload_id, cached=True)

    # Load already transformed strings from the cache.
    def _load_cached_strings(self, session: Session, sources: list[str]) -> dict[str, str]:
        # Load cached source-to-result values for the current transformer version.
        results: dict[str, str] = {}
        # Read large inputs in smaller batches.
        for offset in range(0, len(sources), 500):
            rows = session.scalars(
                select(Transformation).where(
                    Transformation.version == self.transformer_version,
                    Transformation.source.in_(sources[offset : offset + 500]),
                )
            )
            results.update((row.source, row.result) for row in rows)
        return results

    # Transform only strings that are missing from the cache.
    def _transform_strings(self, session: Session, sources: list[str]) -> dict[str, str]:
        # Check the cache again after taking the write lock.
        results = self._load_cached_strings(session, sources)
        for source in sources:
            # Call the transformer only for strings that are not already cached.
            if source not in results:
                result = self.transformer(source)
                results[source] = result
                # Save the new transformation result in the cache.
                session.add(
                    Transformation(version=self.transformer_version, source=source, result=result)
                )
        return results

    # Interleave the transformed strings and create the payload ID.
    @staticmethod
    def _build_payload(request: PayloadInput, results: dict[str, str]) -> tuple[str, str]:
        # Interleave transformed values from the two input lists.
        output = ", ".join(
            results[value]
            for pair in zip(request.list_1, request.list_2, strict=True)
            for value in pair
        )
        # The same final output always produces the same SHA-256 ID.
        payload_id = hashlib.sha256(output.encode("utf-8")).hexdigest()
        return output, payload_id
