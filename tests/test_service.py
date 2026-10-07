# Перевірки PayloadService: rollback, версія кешу, SQL-порції та Unicode.
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caching_service.models import Payload, Transformation
from caching_service.schemas import PayloadInput
from caching_service.service import PayloadService
from caching_service.storage import PayloadStorageError


def assert_database_empty(service: PayloadService) -> None:
    # Після rollback у базі не повинно лишитися підтверджених записів.
    with Session(service.engine) as session:
        assert session.scalar(select(func.count()).select_from(Transformation)) == 0
        assert session.scalar(select(func.count()).select_from(Payload)) == 0


def test_transformer_failure_rolls_back_cache(service: PayloadService, transformer: Mock) -> None:
    # Помилка transformer відкочує навіть уже підготовлені перетворення.
    transformer.side_effect = ["GOOD", RuntimeError("external service failed")]
    with pytest.raises(RuntimeError, match="external service failed"):
        service.create(PayloadInput(list_1=["good"], list_2=["bad"]))
    assert_database_empty(service)
    assert list(service.store.directory.iterdir()) == []


def test_file_failure_rolls_back_cache(
    service: PayloadService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Імітуємо повний диск: файл не записано, транзакція бази відкотилась.
    def fail_write(payload_id: str, output: str) -> None:
        raise PayloadStorageError("disk full")

    monkeypatch.setattr(service.store, "write", fail_write)
    with pytest.raises(PayloadStorageError, match="disk full"):
        service.create(PayloadInput(list_1=["a"], list_2=["b"]))
    assert_database_empty(service)


def test_version_change_invalidates_transformations(
    service: PayloadService, transformer: Mock
) -> None:
    # Інша версія вимагає перетворення знову; однаковий output зберігає ID.
    payload = PayloadInput(list_1=["a"], list_2=["b"])
    first = service.create(payload)
    transformer.reset_mock()
    service.transformer_version = "uppercase-v2"
    second = service.create(payload)
    assert second.id == first.id
    assert second.cached is True
    assert transformer.call_count == 2


def test_large_input_uses_chunked_cache_queries(service: PayloadService, transformer: Mock) -> None:
    # 1001 унікальний рядок перевіряє кілька порцій SQL і повторне використання.
    values = [f"value-{index}" for index in range(1001)]
    request = PayloadInput(list_1=values, list_2=values)
    first = service.create(request)
    assert transformer.call_count == 1001
    assert service.create(request).id == first.id
    assert transformer.call_count == 1001


def test_unicode_and_whitespace_are_preserved(service: PayloadService) -> None:
    # Перевіряємо Unicode, пробіли, порожній рядок і коми.
    result = service.create(PayloadInput(list_1=[" привіт ", ""], list_2=["straße", "a, b"]))
    assert service.read(result.id).output == " ПРИВІТ , STRASSE, , A, B"
