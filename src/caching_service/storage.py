import hashlib
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from caching_service.schemas import PayloadOutput


class PayloadStorageError(Exception):
    """The configured payload directory could not be read or written."""


class PayloadStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, payload_id: str) -> Path:
        return self.directory / f"{payload_id}.json"

    def write(self, payload_id: str, output: str) -> None:
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, suffix=".tmp", delete=False
            ) as file:
                temporary_path = Path(file.name)
                file.write(PayloadOutput(output=output).model_dump_json() + "\n")
                file.flush()
                os.fsync(file.fileno())
            # Publish a complete file before the database exposes its identifier.
            os.replace(temporary_path, self.path(payload_id))
        except OSError as exc:
            raise PayloadStorageError("Could not write the payload file") from exc
        finally:
            if temporary_path is not None:
                # Cleanup must not replace the original error or invalidate a published payload.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def read(self, payload_id: str) -> PayloadOutput:
        try:
            payload = PayloadOutput.model_validate_json(
                self.path(payload_id).read_text(encoding="utf-8")
            )
            if hashlib.sha256(payload.output.encode("utf-8")).hexdigest() != payload_id:
                raise PayloadStorageError("Payload file does not match its identifier")
            return payload
        except (OSError, ValidationError, UnicodeError) as exc:
            raise PayloadStorageError("Could not read the payload file") from exc
