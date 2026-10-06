import json
from concurrent.futures import ThreadPoolExecutor
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
    with Session(service.engine) as session:
        return (
            session.scalar(select(func.count()).select_from(Transformation)),
            session.scalar(select(func.count()).select_from(Payload)),
        )


def test_sample_round_trip_and_file(
    client: TestClient, settings: Settings, transformer: Mock
) -> None:
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
    first = client.post("/payload", json=SAMPLE)
    second = client.post("/payload", json=SAMPLE)
    assert second.status_code == 200
    assert second.json() == {"id": first.json()["id"], "cached": True}
    assert transformer.call_count == 6
    assert row_counts(service) == (6, 1)


def test_duplicates_and_overlap_use_only_missing_transformations(
    client: TestClient, transformer: Mock
) -> None:
    response = client.post("/payload", json={"list_1": ["a", "a"], "list_2": ["a", "b"]})
    assert client.get(response.headers["location"]).json() == {"output": "A, A, A, B"}
    assert transformer.call_count == 2
    client.post("/payload", json={"list_1": ["b", "new"], "list_2": ["a", "new"]})
    assert transformer.call_count == 3


def test_equivalent_outputs_reuse_identifier(client: TestClient, service: PayloadService) -> None:
    first = client.post("/payload", json={"list_1": ["hello"], "list_2": ["world"]})
    second = client.post("/payload", json={"list_1": ["HELLO"], "list_2": ["WORLD"]})
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert row_counts(service) == (4, 1)


def test_order_changes_output_and_identifier(client: TestClient, transformer: Mock) -> None:
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
    assert client.post("/payload", json=body).status_code == 422
    transformer.assert_not_called()
    assert row_counts(service) == (0, 0)


def test_malformed_json(client: TestClient, transformer: Mock) -> None:
    response = client.post("/payload", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    transformer.assert_not_called()


def test_empty_lists(client: TestClient, transformer: Mock) -> None:
    response = client.post("/payload", json={"list_1": [], "list_2": []})
    assert response.status_code == 201
    assert client.get(response.headers["location"]).json() == {"output": ""}
    transformer.assert_not_called()


def test_missing_and_invalid_identifiers(client: TestClient) -> None:
    assert client.get(f"/payload/{'0' * 64}").status_code == 404
    assert client.get("/payload/not-a-valid-id").status_code == 422


def test_cache_survives_restart(settings: Settings, transformer: Mock) -> None:
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
    created = client.post("/payload", json=SAMPLE)
    (settings.payload_dir / f"{created.json()['id']}.json").write_text(corruption, encoding="utf-8")
    response = client.get(created.headers["location"])
    assert response.status_code == 503
    assert response.json() == {"detail": "Storage temporarily unavailable"}
    assert client.post("/payload", json=SAMPLE).status_code == 200
    assert client.get(created.headers["location"]).json() == {"output": EXPECTED}
    assert transformer.call_count == 6


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
