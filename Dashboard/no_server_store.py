"""
Local-file storage for No-Server Mode booth sessions.

This module is the ENTIRE database for No-Server Mode: a single JSON file
plus a folder of uploaded icons. It never touches MySQL or Flask, so it
keeps working when the database is down and can be unit-tested in isolation.

All writes are atomic (temp file + os.replace) and serialized by a module
lock, since Flask serves requests on multiple threads.
"""
import json
import os
import threading
import uuid
from pathlib import Path

_LOCK = threading.RLock()
_BASE_DIR = Path(__file__).resolve().parent


def _data_file() -> Path:
    return Path(os.getenv("NO_SERVER_FILE", str(_BASE_DIR / "no_server_sessions.json")))


def _icons_dir() -> Path:
    return Path(os.getenv("NO_SERVER_ICONS_DIR", str(_BASE_DIR / "no_server_icons")))


def _load() -> dict:
    """Read the data file. Missing -> empty. Corrupt -> quarantine + empty."""
    path = _data_file()
    if not path.is_file():
        return {"sessions": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "sessions" not in data:
            raise ValueError("bad shape")
        return data
    except Exception:
        n = 1
        while path.with_suffix(path.suffix + f".corrupt-{n}").exists():
            n += 1
        path.replace(path.with_suffix(path.suffix + f".corrupt-{n}"))
        return {"sessions": {}}


def _save(data: dict) -> None:
    """Atomic write under the module lock."""
    path = _data_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _LOCK:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _summary(s: dict) -> dict:
    return {
        "id": s["id"],
        "name": s["name"],
        "icon": s.get("icon"),
        "song_count": len(s.get("songs", [])),
        "created_at": s.get("created_at"),
        "owner_id": s.get("owner_id"),
        "watcher_enabled": bool(s.get("watcher_enabled", False)),
    }


def list_sessions(owner_id: str | None = None) -> list:
    data = _load()
    sessions = data["sessions"].values()
    if owner_id is not None:
        sessions = [s for s in sessions if s.get("owner_id") == owner_id]
    return [_summary(s) for s in sessions]


def get_session(sid: str) -> dict | None:
    return _load()["sessions"].get(sid)


def create_session(name: str, now: str, owner_id: str | None = None) -> dict:
    with _LOCK:
        data = _load()
        sid = uuid.uuid4().hex[:8]
        data["sessions"][sid] = {
            "id": sid, "name": name.strip() or "Untitled", "icon": None,
            "created_at": now, "owner_id": owner_id, "watcher_enabled": False,
            "now_playing_id": None, "is_paused": False,
            "next_song_id": 1, "songs": [],
        }
        _save(data)
        return _summary(data["sessions"][sid])


def owns(sid: str, owner_id: str) -> bool:
    # A falsy owner (None / "") must never match — otherwise a caller with no
    # user id could match legacy sessions whose owner_id is None.
    if not owner_id:
        return False
    s = get_session(sid)
    return bool(s) and s.get("owner_id") == owner_id


def set_watcher_enabled(sid: str, enabled: bool) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        s["watcher_enabled"] = bool(enabled)
        _save(data)
        return True


def rename_session(sid: str, name: str) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        s["name"] = name.strip() or s["name"]
        _save(data)
        return True


def set_icon(sid: str, filename: str) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        old = s.get("icon")
        if old and old != filename:
            try:
                (_icons_dir() / old).unlink(missing_ok=True)
            except OSError:
                pass
        s["icon"] = filename
        _save(data)
        return True


def delete_session(sid: str) -> bool:
    with _LOCK:
        data = _load()
        if sid not in data["sessions"]:
            return False
        icon = data["sessions"][sid].get("icon")
        del data["sessions"][sid]
        _save(data)
        if icon:
            try:
                (_icons_dir() / icon).unlink(missing_ok=True)
            except OSError:
                pass
        return True


def _ordered(songs: list) -> list:
    """Custom position first (drag order), then chronological — matches the
    bot-mode /api/queue ordering so the booth list looks the same."""
    return sorted(
        songs,
        key=lambda s: (s.get("position") is None, s.get("position", 0), s.get("added_at", "")),
    )


def get_queue(sid: str) -> dict | None:
    s = get_session(sid)
    if not s:
        return None
    return {
        "now_playing_id": s.get("now_playing_id"),
        "is_paused": bool(s.get("is_paused", False)),
        "songs": _ordered(list(s.get("songs", []))),
    }


def add_song(sid: str, url: str, platform: str, emoji: str, username: str, now: str) -> dict | None:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return None
        song = {
            "id": s["next_song_id"], "url": url, "platform": platform,
            "emoji": emoji, "username": username, "played": False,
            "position": None, "added_at": now,
        }
        s["next_song_id"] += 1
        s["songs"].append(song)
        _save(data)
        return song


def mark_played(sid: str, song_id: int) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        hit = next((x for x in s["songs"] if x["id"] == song_id), None)
        if not hit:
            return False
        hit["played"] = True
        s["now_playing_id"] = song_id
        _save(data)
        return True


def rename_song(sid: str, song_id: int, username: str) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        hit = next((x for x in s["songs"] if x["id"] == song_id), None)
        if not hit:
            return False
        hit["username"] = username
        _save(data)
        return True


def delete_song(sid: str, song_id: int) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        before = len(s["songs"])
        s["songs"] = [x for x in s["songs"] if x["id"] != song_id]
        if len(s["songs"]) == before:
            return False
        if s.get("now_playing_id") == song_id:
            s["now_playing_id"] = None
        _save(data)
        return True


def reorder(sid: str, ids: list) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        pos = {sid_: i for i, sid_ in enumerate(ids)}
        for song in s["songs"]:
            song["position"] = pos.get(song["id"])
        _save(data)
        return True


def clear_queue(sid: str) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        s["songs"] = []
        s["now_playing_id"] = None
        _save(data)
        return True


def set_paused(sid: str, paused: bool) -> bool:
    with _LOCK:
        data = _load()
        s = data["sessions"].get(sid)
        if not s:
            return False
        s["is_paused"] = bool(paused)
        _save(data)
        return True


def used_emojis(sid: str) -> set:
    s = get_session(sid)
    if not s:
        return set()
    return {x["emoji"] for x in s.get("songs", []) if x.get("emoji")}
