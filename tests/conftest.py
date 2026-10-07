# Спільна підготовка pytest: тимчасова база/файли, HTTP-клієнт і лічильник transformer.
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from caching_service.api import create_app
from caching_service.config import Settings
from caching_service.service import PayloadService
from caching_service.transformer import transform


@pytest.fixture
def transformer() -> Mock:
    # Mock виконує справжній transform і рахує виклики для перевірки кешу.
    return Mock(wraps=transform)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # tmp_path ізолює дані тесту від data запущеного сервера.
    return Settings(data_dir=tmp_path)


@pytest.fixture
def client(settings: Settings, transformer: Mock) -> Iterator[TestClient]:
    # TestClient запускає lifespan без окремого Uvicorn; yield передає клієнт тесту.
    with TestClient(create_app(settings, transformer)) as client:
        yield client


@pytest.fixture
def service(client: TestClient) -> PayloadService:
    # Даємо тестам прямий доступ до серверної логіки.
    return client.app.state.service
