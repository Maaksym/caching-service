# Перевірка файлів: невдалий replace не псує старий JSON, cleanup не приховує помилку.
import hashlib
from pathlib import Path

import pytest

from caching_service.storage import PayloadStorageError, PayloadStore


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_failed_atomic_replace_preserves_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_failure: bool
) -> None:
    # monkeypatch імітує збій публікації та, окремо, прибирання .tmp.
    store = PayloadStore(tmp_path)
    payload_id = hashlib.sha256(b"original").hexdigest()
    store.write(payload_id, "original")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("cannot replace")

    monkeypatch.setattr("caching_service.storage.os.replace", fail_replace)
    if cleanup_failure:

        def fail_unlink(self: Path, missing_ok: bool = False) -> None:
            raise PermissionError("cannot clean up temporary file")

        monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(PayloadStorageError, match="Could not write"):
        store.write(payload_id, "replacement")
    assert store.read(payload_id).output == "original"
    assert len(list(tmp_path.glob("*.tmp"))) == int(cleanup_failure)
    assert len(list(tmp_path.glob("*.json"))) == 1
