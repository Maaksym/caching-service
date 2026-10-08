# Read and write generated payload JSON files.
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
    # Create the folder used for payload files.
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        # Create the payload directory if it does not exist.
        directory.mkdir(parents=True, exist_ok=True)

    # Build the file path for a payload ID.
    def path(self, payload_id: str) -> Path:
        # Build the JSON file path from the payload ID.
        return self.directory / f"{payload_id}.json"

    # Save the generated payload as a JSON file.
    def write(self, payload_id: str, output: str) -> None:
        # Write to a temporary file first.
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, suffix=".tmp", delete=False
            ) as file:
                temporary_path = Path(file.name)
                # Store the output as JSON.
                file.write(PayloadOutput(output=output).model_dump_json() + "\n")
                # Make sure the temporary file is fully written.
                file.flush()
                os.fsync(file.fileno())
            # Atomically replace the final file with the completed file.
            os.replace(temporary_path, self.path(payload_id))
        except OSError as exc:
            raise PayloadStorageError("Could not write the payload file") from exc
        finally:
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    # Read and validate a saved payload file.
    def read(self, payload_id: str) -> PayloadOutput:
        # Read and validate the stored JSON file.
        try:
            payload = PayloadOutput.model_validate_json(
                self.path(payload_id).read_text(encoding="utf-8")
            )
            # Make sure the file content still matches its payload ID.
            if hashlib.sha256(payload.output.encode("utf-8")).hexdigest() != payload_id:
                raise PayloadStorageError("Payload file does not match its identifier")
            return payload
        except (OSError, ValidationError, UnicodeError) as exc:
            raise PayloadStorageError("Could not read the payload file") from exc
