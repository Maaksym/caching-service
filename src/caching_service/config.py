# Налаштування сервера: звідси api.py бере шляхи до SQLite і папки JSON.
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # CACHE_DATA_DIR може змінити папку; інакше використовуємо data від робочої папки.
    model_config = SettingsConfigDict(env_prefix="CACHE_", extra="forbid")

    data_dir: Path = Path("data")

    @property
    def database_path(self) -> Path:
        # @property дозволяє читати як settings.database_path без дужок.
        return self.data_dir / "cache.sqlite3"

    @property
    def payload_dir(self) -> Path:
        # Тут storage.py зберігатиме файли {id}.json.
        return self.data_dir / "payloads"
