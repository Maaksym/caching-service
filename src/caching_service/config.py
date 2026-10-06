from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CACHE_", extra="forbid")

    data_dir: Path = Path("data")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "cache.sqlite3"

    @property
    def payload_dir(self) -> Path:
        return self.data_dir / "payloads"
