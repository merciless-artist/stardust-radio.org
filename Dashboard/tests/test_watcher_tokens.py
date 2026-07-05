import importlib
import pytest


@pytest.fixture
def tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_TOKENS_FILE", str(tmp_path / "tokens.json"))
    import watcher_tokens
    importlib.reload(watcher_tokens)
    return watcher_tokens


def test_get_or_create_is_stable_per_user(tokens):
    t1 = tokens.get_or_create("u1", "Mara", now="t")
    t2 = tokens.get_or_create("u1", "Mara", now="t")
    assert t1 == t2 and len(t1) > 16


def test_resolve_returns_user(tokens):
    t = tokens.get_or_create("u1", "Mara", now="t")
    info = tokens.resolve(t)
    assert info == {"user_id": "u1", "username": "Mara"}
    assert tokens.resolve("nope") is None
    assert tokens.resolve("") is None


def test_regenerate_replaces_old(tokens):
    old = tokens.get_or_create("u1", "Mara", now="t")
    new = tokens.regenerate("u1", "Mara", now="t")
    assert new != old
    assert tokens.resolve(old) is None
    assert tokens.resolve(new) == {"user_id": "u1", "username": "Mara"}
