# HTTP-перевірки: POST/GET, кеш, помилки, перезапуск і одночасні запити.
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from caching_service.api import create_app
from caching_service.config import Settings
from caching_service.models import Payload, Transformation
from caching_service.service import PayloadService

SAMPLE = {
    "list_1": ["first string", "second string", "third string"],
    "list_2": ["other string", "another string", "last string"],
}
EXPECTED = "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, THIRD STRING, LAST STRING"


def row_counts(service: PayloadService) -> tuple[int, int]:
    # Рахуємо перетворення та payload у базі, щоб виявити зайві записи.
    with Session(service.engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Transformation)),
            session.scalar(select(func.count()).select_from(Payload)),
        )


def test_sample_round_trip_and_file(
    client: TestClient, settings: Settings, transformer: Mock
) -> None:
    # Перший POST -> 201 та ID; GET і файл містять output; шість нових перетворень.
    response = client.post("/payload", json=SAMPLE)
    assert response.status_code == 201
    assert response.json()["cached"] is False
    payload_id = response.json()["id"]
    assert response.headers["location"] == f"/payload/{payload_id}"
    assert client.get(f"/payload/{payload_id}").json() == {"output": EXPECTED}
    file = settings.payload_dir / f"{payload_id}.json"
    assert json.loads(file.read_text(encoding="utf-8")) == {"output": EXPECTED}
    assert transformer.call_count == 6


def test_identical_requests_reuse_id_without_transforming(
    client: TestClient, transformer: Mock, service: PayloadService
) -> None:
    # Повтор -> 200, той самий ID, без додаткових викликів transformer.
    first = client.post("/payload", json=SAMPLE)
    second = client.post("/payload", json=SAMPLE)
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "cached": True}
    assert transformer.call_count == 6
    assert row_counts(service) == (6, 1)


def test_duplicates_and_overlap_use_only_missing_transformations(
    client: TestClient, transformer: Mock
) -> None:
    # Повтори слів і перетин запитів не створюють зайвих перетворень.
    response = client.post("/payload", json={"list_1": ["a", "a"], "list_2": ["a", "b"]})
    assert client.get(response.headers["location"]).json() == {"output": "A, A, A, B"}
    assert transformer.call_count == 2
    client.post("/payload", json={"list_1": ["b", "new"], "list_2": ["a", "new"]})
    assert transformer.call_count == 3


def test_equivalent_outputs_reuse_identifier(client: TestClient, service: PayloadService) -> None:
    # Різні source можуть дати той самий output і один payload ID.
    first = client.post("/payload", json={"list_1": ["hello"], "list_2": ["world"]})
    second = client.post("/payload", json={"list_1": ["HELLO"], "list_2": ["WORLD"]})
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert row_counts(service) == (4, 1)


def test_order_changes_output_and_identifier(client: TestClient, transformer: Mock) -> None:
    # Інший порядок -> новий ID, але відомі слова повторно не обробляються.
    first = client.post("/payload", json={"list_1": ["a", "b"], "list_2": ["c", "d"]})
    second = client.post("/payload", json={"list_1": ["b", "a"], "list_2": ["c", "d"]})
    assert first.json()["id"] != second.json()["id"]
    assert client.get(first.headers["location"]).json() == {"output": "A, C, B, D"}
    assert client.get(second.headers["location"]).json() == {"output": "B, C, A, D"}
    assert transformer.call_count == 4


@pytest.mark.parametrize(
    "body",
    [
        {"list_1": ["a"], "list_2": []},
        {"list_1": [1], "list_2": ["a"]},
        {"list_1": [None], "list_2": ["a"]},
        {"list_1": "a", "list_2": ["a"]},
        {"list_1": []},
        {"list_1": [], "list_2": [], "extra": True},
    ],
)
def test_invalid_requests_do_not_touch_cache(
    client: TestClient, transformer: Mock, service: PayloadService, body: dict
) -> None:
    # parametrize дає різні неправильні дані: 422 до transformer і змін бази.
    assert client.post("/payload", json=body).status_code == 422
    transformer.assert_not_called()
    assert row_counts(service) == (0, 0)


def test_malformed_json(client: TestClient, transformer: Mock) -> None:
    # Синтаксично неправильний JSON відхиляється до основної обробки.
    response = client.post("/payload", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    transformer.assert_not_called()


def test_empty_lists(client: TestClient, transformer: Mock) -> None:
    # Два порожні списки дозволені та дають порожній output.
    response = client.post("/payload", json={"list_1": [], "list_2": []})
    assert response.status_code == 201
    assert client.get(response.headers["location"]).json() == {"output": ""}
    transformer.assert_not_called()


def test_missing_and_invalid_identifiers(client: TestClient) -> None:
    # Правильний, але невідомий ID -> 404; неправильний формат -> 422.
    assert client.get(f"/payload/{'0' * 64}").status_code == 404
    assert client.get("/payload/not-a-valid-id").status_code == 422


def test_cache_survives_restart(settings: Settings, transformer: Mock) -> None:
    # Новий застосунок із тією самою папкою читає кеш без transformer.
    with TestClient(create_app(settings, transformer)) as first_client:
        first = first_client.post("/payload", json=SAMPLE).json()
    transformer.reset_mock()
    with TestClient(create_app(settings, transformer)) as restarted_client:
        assert restarted_client.get(f"/payload/{first['id']}").json() == {"output": EXPECTED}
        assert restarted_client.post("/payload", json=SAMPLE).json() == {
            "id": first["id"],
            "cached": True,
        }
    transformer.assert_not_called()


def test_concurrent_app_instances_do_not_duplicate_transformer_calls(
    settings: Settings, transformer: Mock
) -> None:
    # Separate engines share only the database and payload directory, as separate workers would.
    # Два engine і вісім запитів: один новий payload, кожне слово оброблено один раз.
    with (
        TestClient(create_app(settings, transformer)) as first,
        TestClient(create_app(settings, transformer)) as second,
        ThreadPoolExecutor(max_workers=8) as pool,
    ):
        responses = list(
            pool.map(
                lambda i: (first if i % 2 == 0 else second).post("/payload", json=SAMPLE), range(8)
            )
        )
        assert sum(response.status_code == 201 for response in responses) == 1
        assert sum(response.status_code == 200 for response in responses) == 7
        assert len({response.json()["id"] for response in responses}) == 1
        assert transformer.call_count == 6
        assert row_counts(first.app.state.service) == (6, 1)


def test_missing_file_returns_503_and_post_repairs_it(
    client: TestClient, settings: Settings, transformer: Mock
) -> None:
    # GET відсутнього файлу -> 503; повторний POST відновлює файл із кешу.
    created = client.post("/payload", json=SAMPLE)
    (settings.payload_dir / f"{created.json()['id']}.json").unlink()
    assert client.get(created.headers["location"]).status_code == 503
    response = client.post("/payload", json=SAMPLE)
    assert response.status_code == 200
    assert client.get(response.headers["location"]).json() == {"output": EXPECTED}
    assert transformer.call_count == 6


@pytest.mark.parametrize("corruption", ["invalid", '{"output": "wrong content"}'])
def test_corrupt_file_returns_controlled_error_and_post_repairs_it(
    client: TestClient, settings: Settings, transformer: Mock, corruption: str
) -> None:
    # Пошкоджений JSON або чужий output -> 503; POST ремонтує результат.
    created = client.post("/payload", json=SAMPLE)
    (settings.payload_dir / f"{created.json()['id']}.json").write_text(corruption, encoding="utf-8")
    response = client.get(created.headers["location"])
    assert response.status_code == 503
    assert response.json() == {"detail": "Storage temporarily unavailable"}
    assert client.post("/payload", json=SAMPLE).status_code == 200
    assert client.get(created.headers["location"]).json() == {"output": EXPECTED}
    assert transformer.call_count == 6


def test_health(client: TestClient) -> None:
    # Маршрут /health підтверджує доступ до бази.
    assert client.get("/health").json() == {"status": "ok"}


def test_cached_payload_does_not_wait_for_slow_writer(settings: Settings) -> None:
    # Повністю кешований POST має завершитись, поки інший engine ще тримає writer lock.
    started = Event()
    release = Event()

    def slow_transform(value: str) -> str:
        if value == "slow":
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test did not release the transformer")
        return value.upper()

    with (
        TestClient(create_app(settings, slow_transform)) as first,
        TestClient(create_app(settings, slow_transform)) as second,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        cached_body = {"list_1": ["known"], "list_2": ["ready"]}
        created = first.post("/payload", json=cached_body).json()
        pending = pool.submit(first.post, "/payload", json={"list_1": ["slow"], "list_2": ["new"]})
        try:
            assert started.wait(timeout=5)
            cached = pool.submit(second.post, "/payload", json=cached_body).result(timeout=5)
            assert cached.status_code == 200
            assert cached.json() == {"id": created["id"], "cached": True}
            assert not pending.done()
        finally:
            # Завжди звільняємо потік, навіть якщо перевірка старої поведінки впала.
            release.set()
        assert pending.result(timeout=5).status_code == 201
