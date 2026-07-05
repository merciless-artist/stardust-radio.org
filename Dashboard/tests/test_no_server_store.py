def test_create_and_list_session(store):
    s = store.create_session("Cozy Lofi", now="2026-06-19T10:00:00")
    assert s["name"] == "Cozy Lofi"
    assert len(s["id"]) == 8
    listed = store.list_sessions()
    assert listed == [{
        "id": s["id"], "name": "Cozy Lofi", "icon": None,
        "song_count": 0, "created_at": "2026-06-19T10:00:00",
        "owner_id": None, "watcher_enabled": False,
    }]


def test_rename_session(store):
    s = store.create_session("Old", now="2026-06-19T10:00:00")
    assert store.rename_session(s["id"], "New") is True
    assert store.get_session(s["id"])["name"] == "New"
    assert store.rename_session("nope", "x") is False


def test_delete_session(store):
    s = store.create_session("Bye", now="2026-06-19T10:00:00")
    assert store.delete_session(s["id"]) is True
    assert store.get_session(s["id"]) is None
    assert store.delete_session(s["id"]) is False


def test_set_icon(store):
    s = store.create_session("Pic", now="2026-06-19T10:00:00")
    assert store.set_icon(s["id"], "x.png") is True
    assert store.list_sessions()[0]["icon"] == "x.png"


def test_corrupt_file_is_quarantined(store, tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("{ not json", encoding="utf-8")
    assert store.list_sessions() == []
    assert (tmp_path / "sessions.json.corrupt-1").exists()


def _mk(store):
    return store.create_session("Q", now="2026-06-19T10:00:00")["id"]


def test_add_song_and_get_queue(store):
    sid = _mk(store)
    song = store.add_song(sid, "https://suno.com/song/a", "Suno", "*",
                          "Host", now="2026-06-19T10:01:00")
    assert song["id"] == 1
    q = store.get_queue(sid)
    assert q["is_paused"] is False and q["now_playing_id"] is None
    assert len(q["songs"]) == 1 and q["songs"][0]["url"] == "https://suno.com/song/a"


def test_add_song_unknown_session(store):
    assert store.add_song("nope", "u", "Suno", "*", "Host", now="t") is None


def test_song_ids_increment(store):
    sid = _mk(store)
    a = store.add_song(sid, "u1", "Suno", "*", "Host", now="t")
    b = store.add_song(sid, "u2", "Suno", "*", "Host", now="t")
    assert (a["id"], b["id"]) == (1, 2)


def test_mark_played_sets_now_playing(store):
    sid = _mk(store)
    s = store.add_song(sid, "u", "Suno", "*", "Host", now="t")
    assert store.mark_played(sid, s["id"]) is True
    q = store.get_queue(sid)
    assert q["now_playing_id"] == s["id"] and q["songs"][0]["played"] is True


def test_rename_and_delete_song(store):
    sid = _mk(store)
    s = store.add_song(sid, "u", "Suno", "*", "Host", now="t")
    assert store.rename_song(sid, s["id"], "Mara") is True
    assert store.get_queue(sid)["songs"][0]["username"] == "Mara"
    assert store.delete_song(sid, s["id"]) is True
    assert store.get_queue(sid)["songs"] == []


def test_reorder_sets_position(store):
    sid = _mk(store)
    a = store.add_song(sid, "u1", "Suno", "*", "Host", now="t")
    b = store.add_song(sid, "u2", "Suno", "*", "Host", now="t")
    assert store.reorder(sid, [b["id"], a["id"]]) is True
    ids = [s["id"] for s in store.get_queue(sid)["songs"]]
    assert ids == [b["id"], a["id"]]


def test_clear_queue(store):
    sid = _mk(store)
    store.add_song(sid, "u", "Suno", "*", "Host", now="t")
    assert store.clear_queue(sid) is True
    assert store.get_queue(sid)["songs"] == []


def test_used_emojis(store):
    sid = _mk(store)
    store.add_song(sid, "u", "Suno", "@", "Host", now="t")
    assert store.used_emojis(sid) == {"@"}


def test_create_session_stamps_owner_and_toggle(store):
    s = store.create_session("Owned", now="t", owner_id="u1")
    full = store.get_session(s["id"])
    assert full["owner_id"] == "u1"
    assert full["watcher_enabled"] is False


def test_list_sessions_filters_by_owner(store):
    a = store.create_session("A", now="t", owner_id="u1")["id"]
    b = store.create_session("B", now="t", owner_id="u2")["id"]
    ids_u1 = [s["id"] for s in store.list_sessions(owner_id="u1")]
    assert a in ids_u1 and b not in ids_u1
    # owner_id=None means "all" (used by tests/admin only)
    assert len(store.list_sessions()) == 2


def test_owns(store):
    sid = store.create_session("X", now="t", owner_id="u1")["id"]
    assert store.owns(sid, "u1") is True
    assert store.owns(sid, "u2") is False
    assert store.owns("missing", "u1") is False


def test_set_watcher_enabled(store):
    sid = store.create_session("X", now="t", owner_id="u1")["id"]
    assert store.set_watcher_enabled(sid, True) is True
    assert store.get_session(sid)["watcher_enabled"] is True
    assert store.list_sessions(owner_id="u1")[0]["watcher_enabled"] is True
    assert store.set_watcher_enabled("missing", True) is False


def test_concurrent_add_song_keeps_all_with_unique_ids(store):
    import threading
    sid = store.create_session("C", now="t")["id"]

    def worker(i):
        store.add_song(sid, f"u{i}", "Suno", "*", "Host", now="t")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    songs = store.get_queue(sid)["songs"]
    ids = [s["id"] for s in songs]
    assert len(songs) == 25
    assert len(set(ids)) == 25
