"""
Personal watcher tokens for No-Server Mode.

Maps a bearer token to the Discord user who owns it, so a host's
screen-watcher (a headless script, no browser session) can authenticate to
the dashboard over the internet. One token per user; regenerate replaces it.
File-backed (no MySQL), atomic writes under a lock — same pattern as
no_server_store.
"""
import json
import os
import secrets
import threading
from pathlib import Path

_LOCK = threading.RLock()
_BASE_DIR = Path(__file__).resolve().parent


def _file() -> Path:
    return Path(os.getenv("WATCHER_TOKENS_FILE", str(_BASE_DIR / "watcher_tokens.json")))


def _load() -> dict:
    path = _file()
    if not path.is_file():
        return {"tokens": {}}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "tokens" not in data:
            raise ValueError("bad shape")
        return data
    except Exception:
        n = 1
        while path.with_suffix(path.suffix + f".corrupt-{n}").exists():
            n += 1
        path.replace(path.with_suffix(path.suffix + f".corrupt-{n}"))
        return {"tokens": {}}


def _save(data: dict) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _LOCK:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _existing_for(data: dict, user_id: str) -> str | None:
    for tok, info in data["tokens"].items():
        if info.get("user_id") == user_id:
            return tok
    return None


def get_or_create(user_id: str, username: str, now: str) -> str:
    with _LOCK:
        data = _load()
        tok = _existing_for(data, user_id)
        if tok:
            return tok
        tok = secrets.token_urlsafe(24)
        data["tokens"][tok] = {"user_id": user_id, "username": username, "created_at": now}
        _save(data)
        return tok


def regenerate(user_id: str, username: str, now: str) -> str:
    with _LOCK:
        data = _load()
        old = _existing_for(data, user_id)
        if old:
            del data["tokens"][old]
        tok = secrets.token_urlsafe(24)
        data["tokens"][tok] = {"user_id": user_id, "username": username, "created_at": now}
        _save(data)
        return tok


def resolve(token: str) -> dict | None:
    if not token:
        return None
    info = _load()["tokens"].get(token)
    if not info:
        return None
    return {"user_id": info["user_id"], "username": info.get("username", "")}
