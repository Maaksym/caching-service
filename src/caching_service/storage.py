# Файлове сховище: service.py викликає write()/read() для data/payloads/{id}.json.
import hashlib
import os
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from caching_service.schemas import PayloadOutput


class PayloadStorageError(Exception):
    """The configured payload directory could not be read or written."""

    # api.py обробляє цей тип помилки і повертає HTTP 503.


class PayloadStore:
    def __init__(self, directory: Path) -> None:
        # Готуємо папку один раз при створенні сервісу.
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, payload_id: str) -> Path:
        # Оператор / у Path з'єднує частини файлового шляху.
        return self.directory / f"{payload_id}.json"

    def write(self, payload_id: str, output: str) -> None:
        # Спочатку пишемо тимчасовий файл, потім публікуємо готовий JSON.
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, suffix=".tmp", delete=False
            ) as file:
                temporary_path = Path(file.name)
                # PayloadOutput із schemas.py задає формат {"output": "..."}.
                file.write(PayloadOutput(output=output).model_dump_json() + "\n")
                # Відправляємо буфери до ОС і синхронізуємо вміст файлу з диском.
                file.flush()
                os.fsync(file.fileno())
            # Файл уже закритий; replace замінює кінцевий файл повністю готовим.
            os.replace(temporary_path, self.path(payload_id))
        except OSError as exc:
            # Уніфікуємо файлову помилку, зберігаючи її первинну причину.
            raise PayloadStorageError("Could not write the payload file") from exc
        finally:
            if temporary_path is not None:
                # Помилка прибирання не має приховати основну помилку чи зіпсувати успіх.
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)

    def read(self, payload_id: str) -> PayloadOutput:
        # [GET 3] Читаємо UTF-8 JSON і перевіряємо його через PayloadOutput.
        try:
            payload = PayloadOutput.model_validate_json(
                self.path(payload_id).read_text(encoding="utf-8")
            )
            # Навіть правильний JSON може містити чужий output: його хеш має збігтися з ID.
            if hashlib.sha256(payload.output.encode("utf-8")).hexdigest() != payload_id:
                raise PayloadStorageError("Payload file does not match its identifier")
            return payload
        except (OSError, ValidationError, UnicodeError) as exc:
            raise PayloadStorageError("Could not read the payload file") from exc
