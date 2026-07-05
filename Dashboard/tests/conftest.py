import os
import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh no_server_store pointed at a temp data dir."""
    monkeypatch.setenv("NO_SERVER_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("NO_SERVER_ICONS_DIR", str(tmp_path / "icons"))
    import importlib
    import no_server_store
    importlib.reload(no_server_store)
    return no_server_store
