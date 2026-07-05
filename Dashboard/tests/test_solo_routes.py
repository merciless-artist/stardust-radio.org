import importlib
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_SERVER_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("NO_SERVER_ICONS_DIR", str(tmp_path / "icons"))
    monkeypatch.setenv("MYSQL_HOST", "127.0.0.1")
    monkeypatch.setenv("MYSQL_PORT", "1")  # dead port — proves no DB needed
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    monkeypatch.setenv("WATCHER_TOKENS_FILE", str(tmp_path / "tokens.json"))
    import no_server_store
    importlib.reload(no_server_store)
    import watcher_tokens
    importlib.reload(watcher_tokens)
    import server
    importlib.reload(server)
    server.app.config["TESTING"] = True
    c = server.app.test_client()
    with c.session_transaction() as sess:
        sess["dj_authorized"] = True
        sess["dj_user_id"] = "owner-1"
    return c


def _client_as(server_module, user_id):
    c = server_module.app.test_client()
    with c.session_transaction() as sess:
        sess["dj_authorized"] = True
        sess["dj_user_id"] = user_id
    return c


def test_create_list_and_queue_shape(client):
    r = client.post("/api/solo/sessions", json={"name": "Cozy"})
    assert r.status_code == 200
    sid = r.get_json()["id"]

    r = client.get("/api/solo/sessions")
    assert any(s["id"] == sid for s in r.get_json())

    r = client.get(f"/api/solo/sessions/{sid}/queue")
    body = r.get_json()
    assert set(body) == {"now_playing_link_id", "is_paused", "songs"}


def test_add_rejects_bad_url(client):
    sid = client.post("/api/solo/sessions", json={"name": "x"}).get_json()["id"]
    r = client.post(f"/api/solo/sessions/{sid}/queue/add", json={"url": "ftp://x"})
    assert r.status_code == 400 and "error" in r.get_json()


def test_add_rejects_unknown_platform(client):
    sid = client.post("/api/solo/sessions", json={"name": "x"}).get_json()["id"]
    r = client.post(f"/api/solo/sessions/{sid}/queue/add",
                    json={"url": "https://example.com/track"})
    assert r.status_code == 400 and "error" in r.get_json()


def test_add_youtube_then_appears_in_queue(client):
    sid = client.post("/api/solo/sessions", json={"name": "x"}).get_json()["id"]
    r = client.post(f"/api/solo/sessions/{sid}/queue/add",
                    json={"url": "https://youtu.be/abc", "username": "Mara"})
    assert r.status_code == 200
    songs = client.get(f"/api/solo/sessions/{sid}/queue").get_json()["songs"]
    assert len(songs) == 1
    assert songs[0]["platform"] == "YouTube" and songs[0]["username"] == "Mara"


def test_unknown_session_404(client):
    assert client.get("/api/solo/sessions/nope/queue").status_code == 404


def test_icon_round_trip(client):
    import io
    sid = client.post("/api/solo/sessions", json={"name": "Pic"}).get_json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    r = client.post(
        f"/api/solo/sessions/{sid}/icon",
        data={"icon": (io.BytesIO(png), "art.png")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200 and r.get_json().get("ok") is True
    listed = client.get("/api/solo/sessions").get_json()
    icon = next(s["icon"] for s in listed if s["id"] == sid)
    assert icon == f"{sid}.png"
    served = client.get(f"/booth/solo_icons/{icon}")
    assert served.status_code == 200
    assert served.mimetype == "image/png"


def test_mutation_on_missing_session_404(client):
    assert client.delete("/api/solo/sessions/nope/queue/1").status_code == 404
    assert client.post(
        "/api/solo/sessions/nope/queue/reorder", json={"ids": [1]}
    ).status_code == 404


def test_booth_page_served_in_solo_mode(client):
    sid = client.post("/api/solo/sessions", json={"name": "x"}).get_json()["id"]
    r = client.get(f"/booth?solo={sid}")
    assert r.status_code == 200
    assert b"add-track-input" in r.data  # booth.html, not a redirect


def test_sessions_are_owner_scoped(client):
    import server
    sid = client.post("/api/solo/sessions", json={"name": "Mine"}).get_json()["id"]
    # a different user cannot see or touch it
    other = _client_as(server, "owner-2")
    assert sid not in [s["id"] for s in other.get("/api/solo/sessions").get_json()]
    assert other.get(f"/api/solo/sessions/{sid}/queue").status_code == 404
    assert other.delete(f"/api/solo/sessions/{sid}").status_code == 404


def test_toggle_watcher(client):
    sid = client.post("/api/solo/sessions", json={"name": "x"}).get_json()["id"]
    r = client.post(f"/api/solo/sessions/{sid}/watcher", json={"enabled": True})
    assert r.status_code == 200
    sess = next(s for s in client.get("/api/solo/sessions").get_json() if s["id"] == sid)
    assert sess["watcher_enabled"] is True


def test_watcher_token_is_stable(client):
    t1 = client.post("/api/solo/watcher-token").get_json()["token"]
    t2 = client.post("/api/solo/watcher-token").get_json()["token"]
    assert t1 == t2 and len(t1) > 16
    t3 = client.post("/api/solo/watcher-token/regenerate").get_json()["token"]
    assert t3 != t1
