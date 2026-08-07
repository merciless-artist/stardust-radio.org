"""Backend routes for the Discord Radio Activity."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server as srv  # noqa: E402

client = srv.app.test_client()


def test_config_js_has_client_id():
    r = client.get("/activity/config.js")
    assert r.status_code == 200
    assert b"1503047790496977067" in r.data
    assert "javascript" in r.headers["Content-Type"]


def test_station_defaults_to_main():
    r = client.get("/api/activity/instance/inst-abc/station")
    assert r.status_code == 200
    assert r.get_json()["station"] == "main"


def test_station_set_and_read_roundtrip():
    p = client.post("/api/activity/instance/inst-xyz/station", json={"station": "kiki"})
    assert p.status_code == 200 and p.get_json()["station"] == "kiki"
    g = client.get("/api/activity/instance/inst-xyz/station")
    assert g.get_json()["station"] == "kiki"


def test_station_rejects_unknown():
    r = client.post("/api/activity/instance/inst-1/station", json={"station": "not_a_station"})
    assert r.status_code == 400


def test_token_requires_code():
    r = client.post("/api/activity/token", json={})
    assert r.status_code == 400


def test_token_exchange_happy(monkeypatch):
    monkeypatch.setattr(srv, "_activity_exchange_code", lambda code: {"access_token": "tok_" + code})
    r = client.post("/api/activity/token", json={"code": "abc"})
    assert r.status_code == 200
    assert r.get_json()["access_token"] == "tok_abc"
