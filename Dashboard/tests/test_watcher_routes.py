import importlib
import pytest


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_SERVER_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("NO_SERVER_ICONS_DIR", str(tmp_path / "icons"))
    monkeypatch.setenv("WATCHER_TOKENS_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYSQL_PORT", "1")
    import no_server_store, watcher_tokens
    importlib.reload(no_server_store)
    importlib.reload(watcher_tokens)
    import server
    importlib.reload(server)
    server.app.config["TESTING"] = True
    # make a session owned by u1 + a token for u1
    s = no_server_store.create_session("S", now="t", owner_id="u1")
    token = watcher_tokens.get_or_create("u1", "Mara", now="t")
    return server.app.test_client(), s["id"], token, no_server_store


def test_watcher_lists_only_its_users_sessions(ctx):
    client, sid, token, store = ctx
    store.create_session("Other", now="t", owner_id="u2")
    r = client.get("/api/watcher/sessions", headers={"X-Watcher-Token": token})
    ids = [s["id"] for s in r.get_json()]
    assert ids == [sid]


def test_watcher_add_blocked_when_toggle_off(ctx):
    client, sid, token, store = ctx
    r = client.post(f"/api/watcher/sessions/{sid}/queue/add",
                    headers={"X-Watcher-Token": token},
                    json={"items": [{"url": "https://suno.com/song/a", "author": "Mara"}]})
    assert r.status_code == 403


def test_watcher_add_works_when_toggle_on_and_dedupes(ctx):
    client, sid, token, store = ctx
    store.set_watcher_enabled(sid, True)
    body = {"items": [
        {"url": "https://suno.com/song/a", "author": "Mara"},
        {"url": "https://suno.com/song/a", "author": "Mara"},  # dup
        {"url": "https://example.com/x", "author": "Z"},        # unknown platform
    ]}
    r = client.post(f"/api/watcher/sessions/{sid}/queue/add",
                    headers={"X-Watcher-Token": token}, json=body)
    assert r.status_code == 200 and r.get_json()["added"] == 1
    songs = store.get_queue(sid)["songs"]
    assert len(songs) == 1 and songs[0]["platform"] == "Suno"


def test_watcher_bad_token_401(ctx):
    client, sid, token, store = ctx
    assert client.get("/api/watcher/sessions").status_code == 401
    assert client.get("/api/watcher/sessions",
                      headers={"X-Watcher-Token": "nope"}).status_code == 401


def test_watcher_cannot_touch_other_users_session(ctx):
    client, sid, token, store = ctx
    other = store.create_session("Other", now="t", owner_id="u2")["id"]
    store.set_watcher_enabled(other, True)
    r = client.post(f"/api/watcher/sessions/{other}/queue/add",
                    headers={"X-Watcher-Token": token},
                    json={"items": [{"url": "https://suno.com/song/b", "author": "x"}]})
    assert r.status_code == 404
