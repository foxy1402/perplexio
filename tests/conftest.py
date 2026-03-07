import importlib
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


APP_MODULES = [
    "app.settings",
    "app.models",
    "app.storage",
    "app.services",
    "app.auth",
    "app.main",
]


def load_fresh_main():
    for name in APP_MODULES:
        if name in sys.modules:
            del sys.modules[name]
    importlib.invalidate_caches()
    return importlib.import_module("app.main")


@pytest.fixture
def client(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, object]]:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("AUTH_PASSWORD", "")
    monkeypatch.setenv("AUTH_SESSION_SECRET", "test-secret")

    main = load_fresh_main()

    async def _noop_index(_file_id: int) -> int:
        return 0

    monkeypatch.setattr(main, "index_file_for_retrieval", _noop_index)

    with TestClient(main.app) as tc:
        yield tc, main
