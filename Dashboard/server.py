"""
Stardust Radio — listener-facing site server.

Serves a multi-channel listener experience:
    /                  — channel picker (home page; lists all stations)
    /<channel>         — per-channel listener page for that station
    /api/stations      — JSON list of all stations (for the picker)
    /api/track-info    — Suno enrichment for the currently playing track
    /api/config.js     — runtime config injected into pages from .env
"""

from __future__ import annotations

import base64
import functools
import io
import json
import os
import random
import re
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Configuration loaded from .env (no secrets in code)
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STATION_NAME: str = os.getenv("STATION_NAME", "Stardust Radio")
ACCENT_COLOR: str = os.getenv("ACCENT_COLOR", "#ff4dd2")

# Public AzuraCast URL — used by the BROWSER for stream/now-playing requests
AZURACAST_BASE: str = os.getenv("AZURACAST_BASE", "https://radio.example.com")

# Internal URL used by THIS server when proxying to AzuraCast (same machine,
# so localhost is faster + avoids round-tripping through Cloudflare)
AZURACAST_INTERNAL: str = os.getenv("AZURACAST_INTERNAL", "http://localhost")

DEFAULT_SHORTCODE: str = os.getenv(
    "AZURACAST_STATION_SHORTCODE", "stardust_radio"
)
HOST: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
PORT: int = int(os.getenv("DASHBOARD_PORT", "8080"))

# ── Discord OAuth (host sign-in) ──
DISCORD_CLIENT_ID:     str = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_CLIENT_SECRET: str = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI:  str = os.getenv(
    "DISCORD_OAUTH_REDIRECT_URI",
    "https://stardust-radio.org/booth/auth/callback",
)
DISCORD_GUILD_ID:      str = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_DJ_ROLE_ID:    str = os.getenv("DISCORD_DJ_ROLE_ID", "")
FLASK_SECRET_KEY:      str = os.getenv("FLASK_SECRET_KEY", secrets.token_urlsafe(32))

# ── Booth (LP host dashboard) — migrated from Utility Bot's dashboard.py ──
DISCORD_BOT_TOKEN:    str = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_WEBHOOK_URL:  str = os.getenv("DISCORD_WEBHOOK_URL", "")
DJ_LOGO_PATH:         str = os.getenv("DJ_LOGO_PATH", "")
MYSQL_HOST:           str = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT:           int = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER:           str = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD:       str = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE:       str = os.getenv("MYSQL_DATABASE", "")


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Flask app
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

app = Flask(
    __name__,
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)

# Cloudflare Tunnel terminates TLS at Cloudflare's edge and forwards plain
# HTTP to localhost. Without ProxyFix, Flask thinks every request is HTTP
# and url_for(_external=True) generates http:// URLs (breaks OAuth redirect
# matching). ProxyFix reads X-Forwarded-Proto and tells Flask the truth.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.secret_key = FLASK_SECRET_KEY
# Sessions live for 12 hours so hosts don't have to re-auth every time
app.permanent_session_lifetime = 60 * 60 * 12
# Cookie config — Lax SameSite is required so the session cookie survives
# the cross-site redirect from Discord back to /booth/auth/callback.
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Page routes — picker (/) + per-channel (/<channel>)
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

# Allowed shortcode characters — letters/digits/underscore/hyphen/dot.
# Anything else goes to 404 to avoid leaking weird URL paths.
_VALID_SHORTCODE = re.compile(r"^[A-Za-z0-9_\-.]+$")

# Reserved paths we don't treat as channel shortcodes
_RESERVED = {"static", "api", "favicon.ico", "robots.txt", "listen", "booth"}


@app.route("/")
def landing() -> Response:
    """
    Landing page — the front door of Stardust Radio.

    Shows the wordmark, tagline, currently-broadcasting stations, and a
    primary "Tune in" CTA into the channel picker. Hosts have a smaller
    sign-in link beneath that takes them to the booth auth flow.
    """
    return send_from_directory(app.static_folder, "landing.html")


@app.route("/listen")
def picker() -> Response:
    """Channel picker — full directory of all stations on the platform."""
    return send_from_directory(app.static_folder, "picker.html")


@app.route("/booth/login")
def booth_login() -> Response:
    """
    Host login page — shows a "Sign in with Discord" button.

    If the user is already signed in (valid session + role check), bounce
    them straight through to /booth instead of showing the login screen.
    """
    if session.get("dj_authorized"):
        return redirect(url_for("booth_home"))
    return send_from_directory(app.static_folder, "booth-login.html")


@app.route("/booth")
def booth_home() -> Response:
    """
    The host booth — gated by Discord OAuth (sign-in only, no role).

    `/booth`              → redirects to the server picker
    `/booth?guild=<id>`   → serves the actual queue management UI

    Anyone without a valid session bounces back to the login page.
    """
    if not session.get("dj_authorized"):
        return redirect(url_for("booth_login"))
    if not request.args.get("guild") and not request.args.get("solo"):
        return redirect(url_for("booth_picker"))
    return send_from_directory(app.static_folder, "booth.html")


@app.route("/booth/logout")
def booth_logout() -> Response:
    """Clear the host session and bounce back to login."""
    session.clear()
    return redirect(url_for("booth_login"))


# ◸──────── ♪ ──────── ◇ ─────── 🔐 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Discord OAuth — host sign-in flow
#
#       Flow:
#         1. User clicks "Sign in with Discord" on /booth/login
#         2. → /booth/auth/start  generates a state token, redirects
#            to discord.com/oauth2/authorize?...
#         3. User approves on Discord, Discord bounces them back to
#            → /booth/auth/callback?code=...&state=...
#         4. We exchange the code for an access token, fetch their
#            member info in our guild (which includes their roles),
#            check for the DJ role, and set the session if approved.
# ◺──────── ♪ ──────── ◇ ─────── 🔐 ─────── ◇ ──────── ♪ ────────◿

DISCORD_OAUTH_SCOPES = "identify guilds.members.read"
DISCORD_OAUTH_AUTHORIZE = "https://discord.com/api/oauth2/authorize"
DISCORD_OAUTH_TOKEN     = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE        = "https://discord.com/api"

# Discord's API requires a sane User-Agent — urllib's default is blocked
# by Cloudflare in front of discord.com (returns 403 Forbidden).
DISCORD_USER_AGENT = (
    "StardustRadio/1.0 (+https://stardust-radio.org) Python-urllib"
)


def _discord_http_error(exc: urllib.error.HTTPError, fallback: str) -> str:
    """
    Pull Discord's actual error message out of a urllib HTTPError so the
    listener gets a useful message instead of just "HTTP Error 403".
    """
    try:
        body = exc.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        # Discord errors usually look like {"error":"...","error_description":"..."}
        # or {"message":"...","code":...}
        msg = (
            parsed.get("error_description")
            or parsed.get("message")
            or parsed.get("error")
            or body[:200]
        )
        return f"{fallback} ({exc.code}: {msg})"
    except Exception:
        return f"{fallback} (HTTP {exc.code})"


@app.route("/booth/auth/start")
def booth_auth_start() -> Response:
    """
    Kick off the OAuth flow — generates a CSRF state, stashes it in the
    session, and redirects to Discord's authorize endpoint.
    """
    if not DISCORD_CLIENT_ID:
        return Response("Discord OAuth is not configured.", status=500)

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session.permanent = True

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": DISCORD_OAUTH_SCOPES,
        "state": state,
    }
    return redirect(f"{DISCORD_OAUTH_AUTHORIZE}?{urllib.parse.urlencode(params)}")


@app.route("/booth/auth/callback")
def booth_auth_callback() -> Response:
    """
    Discord redirects here after the user approves. We verify the state,
    exchange the code for an access token, confirm the user is a member of
    the configured guild, and (if all clear) set the session and bounce them
    to the booth. No role is required — signing in with Discord is enough.
    """
    code  = request.args.get("code", "")
    state = request.args.get("state", "")

    expected_state = session.pop("oauth_state", None)
    # Diagnose state failures specifically — they look the same on the
    # surface but have different root causes.
    if expected_state is None:
        return _login_error(
            "Auth session was lost between start and callback. "
            "Cookies may be blocked, or you took >12h. Try again."
        )
    if not state:
        return _login_error("Discord didn't return a state token. Try again.")
    if state != expected_state:
        return _login_error(
            "Auth state mismatch — likely two browser tabs trying to sign "
            "in at once. Close other tabs and try again."
        )
    if not code:
        return _login_error("Discord didn't return an auth code. Try again.")

    # 1. Exchange code → access token
    token_payload = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  DISCORD_REDIRECT_URI,
    }).encode("utf-8")
    token_req = urllib.request.Request(
        DISCORD_OAUTH_TOKEN,
        data=token_payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept":       "application/json",
            "User-Agent":   DISCORD_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_req, timeout=8) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return _login_error(
            _discord_http_error(exc, "Couldn't exchange code with Discord")
        )
    except Exception as exc:
        return _login_error(f"Couldn't exchange code with Discord: {exc}")

    access_token = token_data.get("access_token")
    if not access_token:
        return _login_error("Discord didn't return an access token.")

    # 2. Fetch guild member info (includes roles)
    member_url = (
        f"{DISCORD_API_BASE}/users/@me/guilds/"
        f"{urllib.parse.quote(DISCORD_GUILD_ID)}/member"
    )
    member_req = urllib.request.Request(
        member_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept":        "application/json",
            "User-Agent":    DISCORD_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(member_req, timeout=8) as resp:
            member = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _login_error(
                "You need to join the configured Discord server first."
            )
        return _login_error(
            _discord_http_error(exc, "Couldn't read your roles from Discord")
        )
    except Exception as exc:
        return _login_error(f"Couldn't reach Discord to check your roles: {exc}")

    user = member.get("user") or {}

    # No role gate: anyone who signs in with Discord AND is a member of the
    # server can use the booth. (The guild-member fetch above already enforces
    # membership — a non-member 404s with "join the Discord first".)

    # 3. All clear — stamp the session
    session["dj_authorized"] = True
    session["dj_user_id"]    = user.get("id")
    session["dj_username"]   = user.get("global_name") or user.get("username") or "DJ"
    session["dj_signed_at"]  = int(time.time())
    session.permanent = True

    return redirect(url_for("booth_home"))


def _login_error(message: str) -> Response:
    """Bounce back to the login page with an error message in the URL."""
    qs = urllib.parse.urlencode({"err": message})
    return redirect(f"{url_for('booth_login')}?{qs}")


@app.route("/<channel>")
def channel_page(channel: str) -> Response:
    """Per-channel listener page for the given station shortcode."""
    if channel in _RESERVED or not _VALID_SHORTCODE.match(channel):
        abort(404)
    # Same listener template for every channel — JS reads the shortcode
    # from the URL path and pulls that station's data from AzuraCast.
    return send_from_directory(app.static_folder, "listener.html")


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Config injection (page reads window.RADIO_CONFIG)
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

@app.route("/api/config.js")
def runtime_config() -> Response:
    """Inject runtime config as a JS module the page reads."""
    body = (
        "window.RADIO_CONFIG = Object.freeze({\n"
        f"  stationName: {STATION_NAME!r},\n"
        f"  accentColor: {ACCENT_COLOR!r},\n"
        f"  azuracastBase: {AZURACAST_BASE!r},\n"
        f"  defaultShortcode: {DEFAULT_SHORTCODE!r},\n"
        "});\n"
    )
    return Response(body, mimetype="application/javascript")


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Per-channel theming
#
#       Each channel can have its own theme by dropping assets into
#         static/themes/<shortcode>/
#       and a config file at
#         static/themes/<shortcode>/theme.json
#
#       Any keys missing from the channel's theme.json fall back to the
#       default Stardust Radio gold look. So a channel can override
#       just the accent color, or just the marquee frame, or everything.
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

THEMES_DIR = BASE_DIR / "static" / "themes"
DEFAULT_THEME_KEY = "stardust_radio"


def _load_default_theme() -> dict:
    """Load the default theme (Stardust Radio gold) as the fallback."""
    default_file = THEMES_DIR / DEFAULT_THEME_KEY / "theme.json"
    if default_file.is_file():
        try:
            with default_file.open(encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


@app.route("/api/theme/<channel>")
def get_theme(channel: str) -> Response:
    """
    Return the theme JSON for the given channel.

    Lookup order:
      1. static/themes/<channel>/theme.json (channel-specific overrides)
      2. fall back to the default theme for any missing keys
    """
    if not _VALID_SHORTCODE.match(channel):
        return jsonify({"error": "invalid channel"}), 400

    default = _load_default_theme()
    channel_file = THEMES_DIR / channel / "theme.json"

    if channel_file.is_file():
        try:
            with channel_file.open(encoding="utf-8") as f:
                channel_theme = json.load(f)
            merged = {**default, **channel_theme}
            return jsonify(merged)
        except Exception:
            pass

    # No channel-specific theme — return the default
    return jsonify(default)


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Stations list — proxy to AzuraCast for the picker
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

@app.route("/api/stations")
def list_stations() -> Response:
    """
    Return the list of all stations from AzuraCast in a slimmed-down form
    that's easy for the picker page to render. We strip large fields the
    picker doesn't need.
    """
    try:
        with urllib.request.urlopen(
            f"{AZURACAST_INTERNAL}/api/stations", timeout=6
        ) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return jsonify({"error": str(e), "stations": []}), 502

    slim = []
    for s in data:
        slim.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "shortcode": s.get("shortcode"),
            "description": s.get("description") or "",
            "url": s.get("url"),
            "listen_url": s.get("listen_url"),
            "public_player_url": s.get("public_player_url"),
            "is_public": s.get("is_public", True),
        })
    return jsonify(slim)


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Track-info enrichment (Suno lyrics + music video)
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

_track_cache: dict[str, dict] = {}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@app.route("/api/track-info")
def track_info() -> Response:
    """Suno enrichment: lyrics, music video URL, and the canonical Suno URL."""
    title = (request.args.get("title") or "").strip()
    artist = (request.args.get("artist") or "").strip()

    if not title:
        return jsonify({})

    cache_key = f"{title}::{artist}"
    if cache_key in _track_cache:
        return jsonify(_track_cache[cache_key])

    result: dict = {}
    suno_url = _find_suno_url(title, artist)
    if suno_url:
        result["suno_url"] = suno_url
        result.update(_scrape_suno_page(suno_url))

    _track_cache[cache_key] = result
    return jsonify(result)


def _find_suno_url(title: str, artist: str) -> str:
    """Search Suno for the matching song; return the canonical URL or empty."""
    try:
        query = f"{title} {artist}".strip()
        api_url = (
            "https://studio-api.prod.suno.com/api/search/"
            f"?term={urllib.parse.quote(query)}"
        )
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        results = data.get("result", []) if isinstance(data, dict) else []
        if not results:
            return ""
        top = results[0]
        song_id = top.get("id") or top.get("uuid")
        if not song_id:
            return ""
        return f"https://suno.com/song/{song_id}"
    except Exception:
        return ""


def _decode_json_string_chunk(raw: str) -> str:
    r"""Decode a fragment that lives inside a JSON string literal.

    Suno embeds lyrics inside Next.js push chunks where the content is
    already JSON-escaped (\n, \t, \", \\, \u00XX, …). Our previous
    unescape did just \n and \t and left literal escaped backslashes
    intact — that's why lyric lines were rendering with a trailing
    backslash (the second char of a "\\n" sequence survived after we
    converted the trailing "\n" to a newline).

    Wrapping the chunk in quotes and letting json.loads do the work
    handles every escape sequence the JSON spec defines. We fall back
    to the old partial decode only if the chunk happens to be malformed
    (which still wouldn't be worse than what we shipped before).
    """
    try:
        return json.loads('"' + raw + '"')
    except Exception:
        return (
            raw.replace("\\\\", "\\")
               .replace("\\n",  "\n")
               .replace("\\t",  "\t")
        )


def _scrape_suno_page(url: str) -> dict:
    """Fetch a Suno song page and extract lyrics + video_url + duration."""
    out: dict = {}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return out

    html_norm = html.replace('\\"', '"')

    m = re.search(r'"video_cover_url"\s*:\s*"(https?://[^"]+\.mp4)"', html_norm)
    if m:
        out["video_url"] = m.group(1)

    m = re.search(r'"duration"\s*:\s*(\d+(?:\.\d+)?)', html_norm)
    if m:
        try:
            out["duration"] = float(m.group(1))
        except ValueError:
            pass

    chunks = re.findall(
        r'self\.__next_f\.push\(\[1,"([^"]{50,})"\]\)', html
    )
    for chunk in chunks:
        text = _decode_json_string_chunk(chunk)
        if text.count("\n") > 3 and (
            "[" in text.lower() or text.count("\n") > 8
        ):
            cleaned = re.sub(r"^[0-9a-f]+:T\d+,", "", text).strip()
            if len(cleaned) > 30 and cleaned.count("\n") > 3:
                out["lyrics"] = cleaned
                break

    if "lyrics" not in out:
        idx = html_norm.find('"prompt":"')
        if idx > -1:
            start = idx + len('"prompt":"')
            end = html_norm.find('","', start)
            if end > -1:
                raw = _decode_json_string_chunk(html_norm[start:end]).strip()
                if len(raw) > 50 and raw.count("\n") > 3:
                    out["lyrics"] = raw

    return out


# ◸──────── ♪ ──────── ◇ ─────── 🎛️ ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Booth (LP Host Dashboard) — migrated from Utility Bot
#
#       Hosts sign in via Discord OAuth (handled above), then land on
#       /booth/picker (server selection) → /booth?guild=<id> (the actual
#       queue management UI).
#
#       Backend routes were originally in `1 UTILITY BOT/dashboard.py` —
#       moved here so everything lives in one Flask process behind the
#       same auth wall.
# ◺──────── ♪ ──────── ◇ ─────── 🎛️ ─────── ◇ ──────── ♪ ────────◿


# ── Auth decorator: every booth API endpoint must check the session ──

def require_auth(view):
    """Bounce non-authorized callers back to /booth/login (HTML) or 401 (JSON)."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if session.get("dj_authorized"):
            return view(*args, **kwargs)
        # API callers (XHR/fetch) prefer JSON; HTML page hits get redirected.
        if request.accept_mimetypes.best == "application/json" or \
           request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("booth_login"))
    return wrapper


# ── MySQL connection ──

def _booth_db():
    """Open a fresh MySQL connection — same DB the Utility Bot writes to."""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── Schema migrations (idempotent, run at module load) ──

def _init_booth_tables() -> None:
    """Ensure tables and columns the booth UI needs are present.

    Idempotent — safe to run on every server start. `CREATE TABLE IF NOT
    EXISTS` handles the base case for `now_playing` (the Utility Bot's
    link_tracker cog creates `tracked_links` and `tracking_channels`, but
    not now_playing, so we own that one). `ALTER TABLE ADD COLUMN` calls
    are wrapped in try/except so existing deployments don't error out
    when the column is already present.

    Schema additions in this migration:
      - `now_playing.is_paused`  — pause state for co-host sync
      - `tracked_links.position` — custom drag-reorder slot (NULL = use
        tracked_at fallback so the existing queue behavior is preserved
        until someone drags a song)
    """
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            # Base shape for now_playing — including is_paused from the
            # start so a fresh deployment doesn't need both the CREATE
            # and the ALTER to land successfully.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS now_playing (
                    guild_id BIGINT PRIMARY KEY,
                    link_id INT NOT NULL,
                    is_paused TINYINT(1) DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP
                )
            """)

            # Additive migrations — these are the columns NEW deployments
            # already have via the CREATE above. For existing deployments
            # where the table predates the column, the ALTER catches up.
            for alter_sql in (
                "ALTER TABLE now_playing "
                "ADD COLUMN is_paused TINYINT(1) DEFAULT 0",
                "ALTER TABLE tracked_links "
                "ADD COLUMN position INT DEFAULT NULL",
            ):
                try:
                    cur.execute(alter_sql)
                except Exception:
                    pass  # column already exists — expected on re-runs
        conn.commit()
    finally:
        conn.close()


# Run migrations at import time so every entrypoint (direct, WSGI, tests)
# sees a fully-prepared schema before serving any request.
#
# Wrapped so the dashboard still boots when MySQL is unavailable: No-Server
# Mode and all static pages must work without the database. Bot-mode
# endpoints will surface their own errors when actually used.
try:
    _init_booth_tables()
except Exception as _e:
    print(f"[booth] MySQL migration skipped (DB unavailable): {_e}")


# ── Discord username + guild name resolver (cached) ──

_user_cache:  dict[int, str] = {}
_guild_cache: dict[str, str] = {}


def _resolve_username(user_id: int) -> str:
    """Resolve a Discord user ID → display name. Cached per-process."""
    if user_id in _user_cache:
        return _user_cache[user_id]
    if not DISCORD_BOT_TOKEN:
        _user_cache[user_id] = "Listener"
        return "Listener"
    req = urllib.request.Request(
        f"https://discord.com/api/v10/users/{user_id}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "User-Agent":    DISCORD_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
            name = data.get("global_name") or data.get("username", "Listener")
            _user_cache[user_id] = name
            return name
    except Exception:
        _user_cache[user_id] = "Listener"
        return "Listener"


def _resolve_guild_name(guild_id: str) -> str:
    """Resolve a Discord guild ID → server name. Cached per-process."""
    if guild_id in _guild_cache:
        return _guild_cache[guild_id]
    if not DISCORD_BOT_TOKEN:
        _guild_cache[guild_id] = f"Server {guild_id}"
        return f"Server {guild_id}"
    req = urllib.request.Request(
        f"https://discord.com/api/v10/guilds/{guild_id}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "User-Agent":    DISCORD_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
            name = data.get("name") or f"Server {guild_id}"
            _guild_cache[guild_id] = name
            return name
    except Exception:
        _guild_cache[guild_id] = f"Server {guild_id}"
        return f"Server {guild_id}"


# ── oEmbed-based song title resolver (cached) ──

_title_cache: dict[str, str] = {}

OEMBED_URLS = {
    "YouTube":    "https://www.youtube.com/oembed?url={url}&format=json",
    "SoundCloud": "https://soundcloud.com/oembed?url={url}&format=json",
    "Suno":       "https://studio-api.prod.suno.com/api/oembed?url={url}",
}

# Mozilla-flavored UA for oEmbed/Suno scraping (different host than Discord)
SCRAPE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ── Platform detection (used by manual Add Track endpoint) ──

# File extensions we treat as directly-playable audio (drop them into
# an <audio> element on the dashboard, no platform-specific player).
_AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus",
)


def _scrape_author_name(url: str, platform: str) -> str:
    """Best-effort fetch of the original uploader / artist name for a URL.

    Used by the Manual Add Track endpoint so a song queued from the
    dashboard shows up credited to the actual creator (their Suno handle,
    YouTube channel name, SoundCloud user) instead of the generic
    "Host" fallback.

    Resolution order:

      1. **oEmbed `author_name`** — works for YouTube and SoundCloud
         (Suno's oEmbed response does NOT include author_name, despite
         oEmbed having it as a standard field, so we skip oEmbed for
         Suno entirely).

      2. **Suno page <title> tag** — Suno serves a `<title>{song} by
         {artist} | Suno</title>`. We split on the last " by " before
         " | Suno" so song names that themselves contain " by " (e.g.
         "Song by the River") still resolve to the correct artist.

      3. **Suno page meta description fallback** — `<meta name="...
         content="{song} by {artist} (@{handle})..."/>`. Used only when
         the title scrape failed.

      4. Empty string when everything failed — caller defaults to
         "Host".
    """
    # ── 1. oEmbed (YouTube, SoundCloud) ──
    template = OEMBED_URLS.get(platform)
    if template and platform != "Suno":
        try:
            oembed_url = template.format(url=urllib.parse.quote(url, safe=""))
            req = urllib.request.Request(oembed_url)
            req.add_header("User-Agent", SCRAPE_USER_AGENT)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                name = (data.get("author_name") or "").strip()
                if name:
                    return name
        except Exception as e:
            print(f"[DJ] oEmbed author scrape failed for {platform} "
                  f"{url!r}: {e}")

    # ── 2 & 3. Suno-specific page scrape ──
    if platform == "Suno":
        try:
            # /s/ short links redirect to /song/<UUID> — needed before
            # the page title format is reliable.
            lookup_url = url
            if "/s/" in url:
                lookup_url = _resolve_suno_short_url(url)

            req = urllib.request.Request(lookup_url)
            req.add_header("User-Agent", SCRAPE_USER_AGENT)
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Primary: <title>{song} by {artist} | Suno</title>
            # rsplit on " by " (from the right, max 1 split) so a song
            # name containing " by " still picks the artist correctly.
            m = re.search(r"<title>([^<]+)</title>", html)
            if m:
                title_text = m.group(1).strip()
                if title_text.endswith(" | Suno"):
                    middle = title_text[:-len(" | Suno")]
                    if " by " in middle:
                        artist = middle.rsplit(" by ", 1)[1].strip()
                        if artist:
                            return artist

            # Fallback: <meta name="description" content="... by ARTIST (@...
            m = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+'
                r'content=["\'][^"\']+?\sby\s+([^"\']+?)\s*\(@',
                html,
            )
            if m:
                return m.group(1).strip()
        except Exception as e:
            print(f"[DJ] Suno page author scrape failed for {url!r}: {e}")

    return ""


def _detect_platform(url: str) -> str:
    """Detect a queue platform string from a URL.

    Mirrors the Utility Bot's `_get_platform` for parity with bot-tracked
    links, then falls through to an `Audio` check so direct audio file
    URLs (paste from a CDN, Drive direct link, etc.) become first-class
    queue items.

    Domain checks run BEFORE the audio-extension fallback so Suno's CDN
    URLs (e.g. `cdn1.suno.ai/<uuid>.mp3`) classify as `Suno` and get
    the proper Suno player, not the bare audio player.

    Returns `"Unknown"` when nothing matches — callers should reject the
    URL rather than insert an unplayable row.
    """
    url_lower = url.lower()

    # Domain-based matches — accept any subdomain so cdn1.suno.ai works.
    if "suno.com"     in url_lower or "suno.ai" in url_lower: return "Suno"
    if "producer.ai"  in url_lower:                           return "Producer.ai"
    if "youtube.com"  in url_lower or "youtu.be" in url_lower: return "YouTube"
    if "soundcloud.com" in url_lower:                         return "SoundCloud"
    if "elevenlabs.io"  in url_lower:                         return "ElevenLabs"

    # Direct-audio fallback — parse so `?token=...` after `.mp3` doesn't
    # defeat the suffix check.
    try:
        path = urllib.parse.urlparse(url_lower).path
        if path.endswith(_AUDIO_EXTENSIONS):
            return "Audio"
    except Exception:
        pass

    return "Unknown"


def _resolve_suno_short_url(url: str) -> str:
    """Follow a Suno /s/ short link redirect to its canonical /song/ URL.

    Uses GET instead of HEAD — Suno's edge sometimes rejects HEAD requests
    (returns 4xx with no Location), which silently fell back to the short
    URL and broke every downstream title/oEmbed lookup.
    """
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", SCRAPE_USER_AGENT)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.url
    except Exception as e:
        print(f"[DJ] Suno short URL redirect failed for {url!r}: {e}")
        return url


def _resolve_title(url: str, platform: str) -> str:
    """Look up the song title via oEmbed.

    Only SUCCESSFUL resolutions are cached. A transient failure (network
    blip, rate limit, slow upstream) used to be cached as an empty string
    forever — meaning one bad request blanked the title until the process
    was restarted. Now failures retry on the next call and the exception
    is logged so we can see what's actually breaking.
    """
    if url in _title_cache:
        return _title_cache[url]
    template = OEMBED_URLS.get(platform)
    if not template:
        return ""  # unknown platform — don't poison the cache
    try:
        # Suno: prefer the suno-resolve cache if it already has the title
        if platform == "Suno" and url in _suno_cache:
            cached_title = _suno_cache[url].get("title", "")
            if cached_title:
                _title_cache[url] = cached_title
                return cached_title

        # /s/ short links resolve to /song/UUID first
        lookup_url = url
        if platform == "Suno" and "/s/" in url:
            lookup_url = _resolve_suno_short_url(url)

        oembed_url = template.format(url=urllib.parse.quote(lookup_url, safe=""))
        req = urllib.request.Request(oembed_url)
        req.add_header("User-Agent", SCRAPE_USER_AGENT)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            title = data.get("title", "")
            if title:
                _title_cache[url] = title  # only cache successful resolutions
            return title
    except Exception as e:
        print(f"[DJ] Title resolve failed for {platform} {url!r}: {e}")
        return ""  # don't cache — retry next call


# ── Suno page scraper (cached) — pulls audio/video/image CDN URLs ──

_suno_cache: dict[str, dict] = {}


# ════════════════════════════════════════════════════════════════════
#       Booth UI page routes
# ════════════════════════════════════════════════════════════════════

@app.route("/booth/picker")
@require_auth
def booth_picker() -> Response:
    """Server picker — host chooses which guild's queue to manage."""
    return send_from_directory(app.static_folder, "booth-picker.html")


@app.route("/booth/logo.png")
def booth_logo() -> Response:
    """Serve the booth's brand logo image. Falls back to a 1x1 transparent."""
    if DJ_LOGO_PATH and os.path.exists(DJ_LOGO_PATH):
        return send_file(DJ_LOGO_PATH, mimetype="image/png")
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAB"
        "Nl7BcQAAAABJRU5ErkJggg=="
    )
    return send_file(io.BytesIO(pixel), mimetype="image/png")


@app.route("/api/booth/screensavers")
@require_auth
def booth_screensavers() -> Response:
    """
    List every `.mp4` file inside `static/assets/screensavers/`.

    The booth's away-mode JS calls this on load so any new video the
    host drops into the screensavers folder shows up automatically —
    no code changes or filename conventions needed. Just drop and reload.
    """
    screensavers_dir = BASE_DIR / "static" / "assets" / "screensavers"
    if not screensavers_dir.is_dir():
        return jsonify([])
    files = sorted(
        p.name for p in screensavers_dir.glob("*.mp4") if p.is_file()
    )
    return jsonify([f"/static/assets/screensavers/{name}" for name in files])


@app.route("/booth/guild_icons/<filename>")
def booth_guild_icon(filename: str) -> Response:
    """Serve uploaded per-guild icons — written by /api/guilds/<id>/icon."""
    icons_dir = BASE_DIR / "guild_icons"
    safe_name = Path(filename).name        # strip any path components
    icon_path = icons_dir / safe_name
    if icon_path.is_file():
        return send_file(str(icon_path), mimetype="image/png")
    abort(404)


# ════════════════════════════════════════════════════════════════════
#       Booth API routes (all gated by @require_auth)
# ════════════════════════════════════════════════════════════════════

@app.route("/api/guilds")
@require_auth
def booth_guilds() -> Response:
    """List every guild that has tracked links — for the booth's server picker."""
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT guild_id, COUNT(*) AS song_count,
                       MAX(tracked_at) AS last_active
                FROM tracked_links
                GROUP BY guild_id
                ORDER BY last_active DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    icons_dir = BASE_DIR / "guild_icons"
    for row in rows:
        gid_str = str(row["guild_id"])
        row["guild_id"]   = gid_str
        row["name"]       = _resolve_guild_name(gid_str)
        row["last_active"] = row["last_active"].isoformat() if row["last_active"] else None
        icon_path = icons_dir / f"{gid_str}.png"
        row["icon"] = f"/booth/guild_icons/{gid_str}.png" if icon_path.is_file() else None
    return jsonify(rows)


@app.route("/api/guilds/<guild_id>/icon", methods=["POST"])
@require_auth
def booth_upload_guild_icon(guild_id: str) -> Response:
    """Save a custom icon image for a guild (uploaded from the picker UI)."""
    if "icon" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    icons_dir = BASE_DIR / "guild_icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^0-9]", "", guild_id)[:20]
    if not safe_id:
        return jsonify({"error": "Invalid guild id"}), 400
    request.files["icon"].save(icons_dir / f"{safe_id}.png")
    return jsonify({"ok": True})


@app.route("/api/queue")
@require_auth
def booth_queue() -> Response:
    """Return the current queue (tracked links) for a guild.

    Response shape (object, NOT a bare array — both browsers need the
    session state alongside the songs for co-host sync):

        {
            "now_playing_link_id": 42 | null,
            "is_paused":           false,
            "songs":               [ {...}, {...} ]
        }

    Ordering: custom `position` (set by drag-reorder) wins; songs without
    a position fall back to chronological. This matches what
    `_ordered_queue_ids` uses, so NEXT/BACK and the visible list agree.

    Back-compat: legacy clients that expect a bare array can still drive
    by reading `response.songs` — the old behavior is preserved verbatim
    inside that field.
    """
    guild_id = request.args.get("guild")
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            # Songs first — custom position trumps chronological so a host's
            # drag order persists across both browsers.
            if guild_id:
                cur.execute("""
                    SELECT id, user_id, username, url, platform, emoji, tracked_at
                    FROM tracked_links
                    WHERE guild_id = %s
                    ORDER BY position IS NULL, position ASC, tracked_at ASC
                """, (guild_id,))
            else:
                cur.execute("""
                    SELECT id, user_id, username, url, platform, emoji, tracked_at
                    FROM tracked_links
                    ORDER BY position IS NULL, position ASC, tracked_at ASC
                """)
            links = cur.fetchall()

            # Session state (current song + pause flag) — included in
            # every poll so the Host browser doesn't need a separate
            # endpoint to know what's playing.
            now_link_id: int | None = None
            is_paused = False
            if guild_id:
                cur.execute(
                    "SELECT link_id, is_paused FROM now_playing "
                    "WHERE guild_id = %s",
                    (guild_id,),
                )
                np_row = cur.fetchone()
                if np_row:
                    now_link_id = np_row["link_id"]
                    is_paused   = bool(np_row.get("is_paused", 0))
    finally:
        conn.close()

    # Drop playlist URLs (not playable as a single song)
    links = [l for l in links if "/playlist/" not in (l.get("url") or "")]

    for link in links:
        link["username"] = link.get("username") or _resolve_username(link["user_id"])
        link["title"]    = _resolve_title(link["url"], link["platform"])
        if link["tracked_at"]:
            link["tracked_at"] = link["tracked_at"].isoformat()

    return jsonify({
        "now_playing_link_id": now_link_id,
        "is_paused":           is_paused,
        "songs":               links,
    })


@app.route("/api/queue/<int:link_id>/played", methods=["POST"])
@require_auth
def booth_mark_played(link_id: int) -> Response:
    """Mark a song as played and set it as the current now-playing for its guild."""
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE tracked_links SET played = 1 WHERE id = %s", (link_id,))
            cur.execute("SELECT guild_id FROM tracked_links WHERE id = %s", (link_id,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    INSERT INTO now_playing (guild_id, link_id)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE link_id = VALUES(link_id)
                """, (row["guild_id"], link_id))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/queue/<int:link_id>/rename", methods=["POST"])
@require_auth
def booth_rename_link(link_id: int) -> Response:
    """Rename the listener attribution displayed for a tracked link."""
    data = request.get_json() or {}
    new_name = (data.get("username") or "").strip()
    if not new_name:
        return jsonify({"error": "Name cannot be empty"}), 400
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tracked_links SET username = %s WHERE id = %s",
                (new_name, link_id),
            )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/queue/<int:link_id>", methods=["DELETE"])
@require_auth
def booth_delete_link(link_id: int) -> Response:
    """Remove a single tracked link from the queue."""
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tracked_links WHERE id = %s", (link_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@app.route("/api/queue/clear", methods=["DELETE"])
@require_auth
def booth_clear_queue() -> Response:
    """Clear the queue — for one guild if `guild=...`, otherwise everything."""
    guild_id = request.args.get("guild")
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            if guild_id and guild_id != "0":
                cur.execute("DELETE FROM tracked_links WHERE guild_id = %s", (guild_id,))
            else:
                cur.execute("DELETE FROM tracked_links")
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════
#       No-Server Mode — file-backed booth sessions (no bot, no MySQL)
# ════════════════════════════════════════════════════════════════════
import no_server_store
from datetime import datetime

_SOLO_ICONS_DIR = Path(os.getenv("NO_SERVER_ICONS_DIR", str(BASE_DIR / "no_server_icons")))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _solo_queue_payload(sid: str):
    """Map the stored queue to the SAME shape as /api/queue, resolving
    titles so booth.html can consume it unchanged."""
    q = no_server_store.get_queue(sid)
    if q is None:
        return None
    songs = []
    for s in q["songs"]:
        item = dict(s)
        item["title"] = _resolve_title(s["url"], s["platform"])
        songs.append(item)
    return {
        "now_playing_link_id": q["now_playing_id"],
        "is_paused": q["is_paused"],
        "songs": songs,
    }


def _solo_user() -> str | None:
    return session.get("dj_user_id")


@app.route("/api/solo/sessions", methods=["GET"])
@require_auth
def solo_list_sessions() -> Response:
    # No user id on the session → no sessions (never fall through to the
    # store's None == "all" behavior, which would leak unowned sessions).
    uid = _solo_user()
    if not uid:
        return jsonify([])
    return jsonify(no_server_store.list_sessions(owner_id=uid))


@app.route("/api/solo/sessions", methods=["POST"])
@require_auth
def solo_create_session() -> Response:
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    return jsonify(no_server_store.create_session(name, _now_iso(), owner_id=_solo_user()))


@app.route("/api/solo/sessions/<sid>/rename", methods=["POST"])
@require_auth
def solo_rename_session(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not no_server_store.rename_session(sid, name):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>", methods=["DELETE"])
@require_auth
def solo_delete_session(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    if not no_server_store.delete_session(sid):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/icon", methods=["POST"])
@require_auth
def solo_upload_icon(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    if "icon" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    _SOLO_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    # Sanitize the session id before using it as a filename so the write is
    # self-evidently inside the icons dir (don't rely on the uuid format).
    fname = f"{Path(sid).name}.png"
    request.files["icon"].save(_SOLO_ICONS_DIR / fname)
    no_server_store.set_icon(sid, fname)
    return jsonify({"ok": True})


@app.route("/booth/solo_icons/<filename>")
def solo_icon(filename: str) -> Response:
    safe = Path(filename).name
    path = _SOLO_ICONS_DIR / safe
    if path.is_file():
        return send_file(str(path), mimetype="image/png")
    abort(404)


@app.route("/api/solo/sessions/<sid>/queue", methods=["GET"])
@require_auth
def solo_queue(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    payload = _solo_queue_payload(sid)
    if payload is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(payload)


@app.route("/api/solo/sessions/<sid>/queue/add", methods=["POST"])
@require_auth
def solo_queue_add(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    platform = _detect_platform(url)
    if platform == "Unknown":
        return jsonify({"error": "Unsupported URL — try Suno, YouTube, "
                                 "SoundCloud, Producer.ai, ElevenLabs, or a "
                                 "direct audio file"}), 400
    explicit = (data.get("username") or "").strip()
    username = explicit or _scrape_author_name(url, platform) or "Host"
    used = no_server_store.used_emojis(sid)
    pool = [e for e in _DASHBOARD_SONG_EMOJIS if e not in used]
    emoji = random.choice(pool or list(_DASHBOARD_SONG_EMOJIS))
    song = no_server_store.add_song(sid, url, platform, emoji, username, _now_iso())
    return jsonify({"ok": True, **song})


@app.route("/api/solo/sessions/<sid>/queue/<int:song_id>/played", methods=["POST"])
@require_auth
def solo_played(sid: str, song_id: int) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    if not no_server_store.mark_played(sid, song_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/queue/<int:song_id>/rename", methods=["POST"])
@require_auth
def solo_rename_song(sid: str, song_id: int) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    name = (data.get("username") or "").strip()
    if not name:
        return jsonify({"error": "Name cannot be empty"}), 400
    if not no_server_store.rename_song(sid, song_id, name):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/queue/<int:song_id>", methods=["DELETE"])
@require_auth
def solo_delete_song(sid: str, song_id: int) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    if not no_server_store.delete_song(sid, song_id):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/queue/reorder", methods=["POST"])
@require_auth
def solo_reorder(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not no_server_store.reorder(sid, ids):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/queue/clear", methods=["DELETE"])
@require_auth
def solo_clear(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    if not no_server_store.clear_queue(sid):
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/solo/sessions/<sid>/watcher", methods=["POST"])
@require_auth
def solo_set_watcher(sid: str) -> Response:
    if not no_server_store.owns(sid, _solo_user()):
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    no_server_store.set_watcher_enabled(sid, bool(data.get("enabled")))
    return jsonify({"ok": True, "enabled": bool(data.get("enabled"))})


@app.route("/api/solo/watcher-token", methods=["POST"])
@require_auth
def solo_watcher_token() -> Response:
    import watcher_tokens
    uid = _solo_user() or ""
    name = session.get("dj_username", "")
    return jsonify({"token": watcher_tokens.get_or_create(uid, name, _now_iso())})


@app.route("/api/solo/watcher-token/regenerate", methods=["POST"])
@require_auth
def solo_watcher_token_regen() -> Response:
    import watcher_tokens
    uid = _solo_user() or ""
    name = session.get("dj_username", "")
    return jsonify({"token": watcher_tokens.regenerate(uid, name, _now_iso())})


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Watcher-facing routes (token auth, used by dj-watcher.py)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

def _watcher_user() -> dict | None:
    """Resolve the X-Watcher-Token header to its owner, or None."""
    import watcher_tokens
    return watcher_tokens.resolve(request.headers.get("X-Watcher-Token", ""))


@app.route("/api/watcher/sessions", methods=["GET"])
def watcher_sessions() -> Response:
    user = _watcher_user()
    if not user:
        return jsonify({"error": "bad or missing watcher token"}), 401
    sessions = no_server_store.list_sessions(owner_id=user["user_id"])
    return jsonify([
        {"id": s["id"], "name": s["name"], "watcher_enabled": s["watcher_enabled"]}
        for s in sessions
    ])


@app.route("/api/watcher/sessions/<sid>/queue/add", methods=["POST"])
def watcher_queue_add(sid: str) -> Response:
    user = _watcher_user()
    if not user:
        return jsonify({"error": "bad or missing watcher token"}), 401
    if not no_server_store.owns(sid, user["user_id"]):
        return jsonify({"error": "session not found"}), 404
    sess = no_server_store.get_session(sid)
    if not sess:
        return jsonify({"error": "session not found"}), 404
    if not sess.get("watcher_enabled"):
        return jsonify({"error": "auto-add is turned off for this session"}), 403

    data = request.get_json(silent=True) or {}
    items = data.get("items") or []
    existing = {s["url"] for s in sess.get("songs", [])}
    added = 0
    for item in items:
        url = (item.get("url") or "").strip()
        if not url or url in existing:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        platform = _detect_platform(url)
        if platform == "Unknown":
            continue
        author = (item.get("author") or "").strip() or "Listener"
        used = no_server_store.used_emojis(sid)
        pool = [e for e in _DASHBOARD_SONG_EMOJIS if e not in used]
        emoji = random.choice(pool or list(_DASHBOARD_SONG_EMOJIS))
        no_server_store.add_song(sid, url, platform, emoji, author, _now_iso())
        existing.add(url)
        added += 1
    return jsonify({"added": added})


# ════════════════════════════════════════════════════════════════════
#       Co-host sync — playback controls that write through to the DB
#       so both DJ and Host browsers can drive the same session.
# ════════════════════════════════════════════════════════════════════

# Emoji pool for manually-added tracks. Mirrors the Utility Bot's
# link_tracker SONG_EMOJIS so manual adds blend in with bot-tracked ones.
_DASHBOARD_SONG_EMOJIS: tuple[str, ...] = (
    "🎸", "🎹", "🥁", "🎺", "🎻", "🎷", "🪗", "🪘", "🪕", "🎤",
    "🦋", "🔥", "💎", "⚡", "🌙", "☀️", "🌊", "🍀", "🦊", "🐉",
    "🎯", "🏆", "👑", "💫", "🌟", "🎪", "🎭", "🎨", "🧊", "🫧",
    "🍒", "🍭", "🌸", "🌺", "🍄", "🪐", "🗡️", "🛸", "🎃", "🦄",
    "🐝", "🦅", "🐬", "🦩", "🌵", "🍉", "🧿", "💀", "🤖", "👾",
)


def _ordered_queue_ids(cur, guild_id: str) -> list[int]:
    """Return the playable link IDs for a guild in display order.

    Mirrors the ORDER BY used by /api/queue (custom `position` first,
    then chronological) and drops playlist URLs the same way the queue
    endpoint does — so NEXT/BACK skip exactly what the player would skip.
    """
    cur.execute(
        """
        SELECT id, url
          FROM tracked_links
         WHERE guild_id = %s
         ORDER BY position IS NULL, position ASC, tracked_at ASC
        """,
        (guild_id,),
    )
    return [
        row["id"]
        for row in cur.fetchall()
        if "/playlist/" not in (row.get("url") or "")
    ]


def _advance_now_playing(guild_id: str, direction: int) -> tuple[int | None, int]:
    """Move the guild's now-playing pointer forward or back in the queue.

    `direction` is +1 for NEXT, -1 for BACK. Wraps at both ends so end-of-
    queue NEXT loops back to the first song (matches existing playSong()
    loop behavior in booth.html), and start-of-queue BACK wraps to the
    last song.

    Returns (new_link_id, http_status). new_link_id is None on error.
    """
    if not guild_id:
        return None, 400
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            ordered = _ordered_queue_ids(cur, guild_id)
            if not ordered:
                return None, 409  # nothing in the queue to advance to

            # Find the current song's index. Missing now_playing OR a stale
            # link_id (song was deleted) both fall through to "start from
            # the top".
            cur.execute(
                "SELECT link_id FROM now_playing WHERE guild_id = %s",
                (guild_id,),
            )
            row = cur.fetchone()
            current_id = row["link_id"] if row else None
            try:
                idx = ordered.index(current_id) if current_id else -1
            except ValueError:
                idx = -1  # current song no longer in queue

            new_idx = (idx + direction) % len(ordered)
            new_link_id = ordered[new_idx]

            # Mirror booth_mark_played — bump played flag + upsert now_playing.
            # Pause state is preserved (NEXT shouldn't auto-unpause).
            cur.execute(
                "UPDATE tracked_links SET played = 1 WHERE id = %s",
                (new_link_id,),
            )
            cur.execute(
                """
                INSERT INTO now_playing (guild_id, link_id)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE link_id = VALUES(link_id)
                """,
                (guild_id, new_link_id),
            )
        conn.commit()
        return new_link_id, 200
    finally:
        conn.close()


@app.route("/api/dj/next", methods=["POST"])
@require_auth
def booth_dj_next() -> Response:
    """Advance the now-playing pointer to the next song.

    Either the DJ or the Host browser can call this. The DJ's browser
    will pick up the change on its next 2s queue poll and load the new
    song; the Host's browser will refresh its thumbnail card.
    """
    guild_id = request.args.get("guild")
    new_id, status = _advance_now_playing(guild_id, +1)
    if new_id is None:
        return jsonify({"error": "Cannot advance — queue empty or no guild"}), status
    return jsonify({"ok": True, "link_id": new_id})


@app.route("/api/dj/back", methods=["POST"])
@require_auth
def booth_dj_back() -> Response:
    """Step the now-playing pointer back one song (wraps at the start)."""
    guild_id = request.args.get("guild")
    new_id, status = _advance_now_playing(guild_id, -1)
    if new_id is None:
        return jsonify({"error": "Cannot go back — queue empty or no guild"}), status
    return jsonify({"ok": True, "link_id": new_id})


@app.route("/api/dj/pause", methods=["POST"])
@require_auth
def booth_dj_pause() -> Response:
    """Set the shared pause state for the guild's session.

    Body: `{"paused": true|false}`. Explicit value (not "toggle the
    existing one") so two browsers racing this can't accidentally land
    on opposite states — last writer wins, both reconverge.
    """
    guild_id = request.args.get("guild")
    if not guild_id:
        return jsonify({"error": "guild query param required"}), 400
    data = request.get_json() or {}
    paused = bool(data.get("paused", False))

    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            # Only flips the flag on an existing now_playing row — if the
            # session hasn't started yet (no song played), there's nothing
            # to pause and we no-op rather than create a phantom row.
            cur.execute(
                "UPDATE now_playing SET is_paused = %s WHERE guild_id = %s",
                (1 if paused else 0, guild_id),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "paused": paused})


@app.route("/api/queue/reorder", methods=["POST"])
@require_auth
def booth_queue_reorder() -> Response:
    """Persist a custom drag-and-drop ordering for the guild's queue.

    Body: `{"ordered_link_ids": [123, 456, 789, ...]}`. Each ID gets a
    `position` matching its index in the array. IDs not in the payload
    keep their existing `position` (so partial reorders work, though the
    frontend will normally send the whole list).
    """
    guild_id = request.args.get("guild")
    if not guild_id:
        return jsonify({"error": "guild query param required"}), 400
    data = request.get_json() or {}
    ordered = data.get("ordered_link_ids") or []
    if not isinstance(ordered, list):
        return jsonify({"error": "ordered_link_ids must be a list"}), 400

    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            # Guard against cross-guild ID injection — only update IDs that
            # actually belong to the requested guild.
            for idx, link_id in enumerate(ordered):
                try:
                    link_id_int = int(link_id)
                except (TypeError, ValueError):
                    continue
                cur.execute(
                    """
                    UPDATE tracked_links
                       SET position = %s
                     WHERE id = %s AND guild_id = %s
                    """,
                    (idx, link_id_int, guild_id),
                )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "count": len(ordered)})


@app.route("/api/queue/add", methods=["POST"])
@require_auth
def booth_queue_add() -> Response:
    """Manually add a track to the queue from the dashboard.

    Body: `{"url": "https://...", "username": "Host"?}`. The platform is
    auto-detected from the URL; if no rule matches, returns 400. Manual
    adds are credited to `"Host"` by default and tied to the guild's
    current tracking channel (so they're cleaned up by !stoptracking
    along with the bot-tracked ones).
    """
    guild_id = request.args.get("guild")
    if not guild_id:
        return jsonify({"error": "guild query param required"}), 400

    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400

    platform = _detect_platform(url)
    if platform == "Unknown":
        return jsonify({"error": "Unsupported URL — try Suno, YouTube, "
                                 "SoundCloud, Producer.ai, ElevenLabs, or a "
                                 "direct audio file (.mp3/.m4a/.wav/.ogg/"
                                 ".flac/.opus)"}), 400

    # Credit priority:
    #   1. Explicit username from the request body (user typed one)
    #   2. Scraped author from the platform's oEmbed (Suno handle,
    #      YouTube channel, SoundCloud user) — accurate when available
    #   3. "Host" as a sensible fallback
    # The user can still rename via the existing pencil-edit on the
    # queue row after the fact.
    explicit_name = (data.get("username") or "").strip()
    scraped_name  = "" if explicit_name else _scrape_author_name(url, platform)
    username = explicit_name or scraped_name or "Host"

    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            # Tie the manual add to whatever channel is currently being
            # tracked for this guild (if any). That way !stoptracking
            # will sweep it up with the rest. If no tracking is active,
            # fall through to 0 — the row is still playable but won't
            # be auto-cleaned (the dashboard's clear-all still works).
            cur.execute(
                "SELECT channel_id FROM tracking_channels "
                "WHERE guild_id = %s LIMIT 1",
                (guild_id,),
            )
            tc_row = cur.fetchone()
            channel_id = tc_row["channel_id"] if tc_row else 0

            # Pick an unused emoji per guild so manual adds visually
            # blend in with bot-tracked entries.
            cur.execute(
                "SELECT emoji FROM tracked_links "
                "WHERE guild_id = %s AND emoji IS NOT NULL",
                (guild_id,),
            )
            used = {r["emoji"] for r in cur.fetchall()}
            available = [e for e in _DASHBOARD_SONG_EMOJIS if e not in used]
            emoji = random.choice(available or list(_DASHBOARD_SONG_EMOJIS))

            cur.execute(
                """
                INSERT INTO tracked_links
                       (guild_id, channel_id, user_id, username,
                        url, platform, emoji, played, position)
                VALUES (%s,        %s,         %s,      %s,
                        %s,  %s,       %s,    0,      NULL)
                """,
                (guild_id, channel_id, 0, username, url, platform, emoji),
            )
            new_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "id": new_id,
        "platform": platform,
        "emoji": emoji,
        "username": username,
        "url": url,
    })


@app.route("/api/suno-resolve")
@require_auth
def booth_suno_resolve() -> Response:
    """
    Resolve a Suno page URL to playable CDN URLs (audio + video + image + title).

    Scrapes the Suno page HTML and pulls audio_url / video_cover_url / image_url
    out of the embedded JSON. Caches results so repeated lookups are free.
    """
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if url in _suno_cache:
        return jsonify(_suno_cache[url])

    song_uuid: str | None = None
    uuid_match = re.search(r"/song/([a-f0-9-]{36})", url)
    if uuid_match:
        song_uuid = uuid_match.group(1)

    audio_url = video_url = image_url = title = lyrics = ""
    duration: float | None = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": SCRAPE_USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            final_url = resp.url
            html = resp.read().decode("utf-8", errors="replace")

        if not song_uuid:
            uuid_match = re.search(r"/song/([a-f0-9-]{36})", final_url)
            if uuid_match:
                song_uuid = uuid_match.group(1)
        if not song_uuid:
            uuid_match = re.search(
                r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
                html,
            )
            if uuid_match:
                song_uuid = uuid_match.group(0)

        # Next.js double-escapes JSON; normalize so regexes match.
        html_norm = html.replace('\\"', '"')

        m = re.search(r'"video_cover_url"\s*:\s*"(https?://[^"]+\.mp4)"', html_norm)
        if m:
            video_url = m.group(1)
        m = re.search(r'"audio_url"\s*:\s*"(https?://[^"]+\.mp3)"', html_norm)
        if m:
            audio_url = m.group(1)
        if not audio_url:
            m = re.search(r"https://cdn\d+\.suno\.ai/[a-f0-9-]{36}\.mp3", html_norm)
            if m:
                audio_url = m.group(0)
        m = re.search(r'"image_url"\s*:\s*"(https?://[^"]+)"', html_norm)
        if m:
            image_url = m.group(1)
        if not image_url:
            m = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']',
                html,
            )
            if m:
                image_url = m.group(1)
        m = re.search(r'"title"\s*:\s*"([^"]+)"', html_norm)
        if m:
            title = m.group(1)
        if not title:
            m = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
                html,
            )
            if m:
                title = m.group(1)

        # Duration in seconds — useful for approximate lyric pacing
        m = re.search(r'"duration"\s*:\s*(\d+(?:\.\d+)?)', html_norm)
        if m:
            try:
                duration = float(m.group(1))
            except ValueError:
                duration = None

        # Lyrics — Suno embeds them in Next.js push chunks. Look for the
        # chunk that has lots of newlines (real song lyrics) and pull it.
        # Use _decode_json_string_chunk so escape sequences (including
        # literal backslashes encoded as \\\\) come through cleanly.
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"([^"]{50,})"\]\)', html
        )
        for chunk in chunks:
            text = _decode_json_string_chunk(chunk)
            if text.count("\n") > 3 and (
                "[" in text.lower() or text.count("\n") > 8
            ):
                cleaned = re.sub(r"^[0-9a-f]+:T\d+,", "", text).strip()
                if len(cleaned) > 30 and cleaned.count("\n") > 3:
                    lyrics = cleaned
                    break

        # Fallback path: the "prompt" field on the song record often holds
        # the same lyric text. Cheap secondary source if the chunk scan misses.
        if not lyrics:
            idx = html_norm.find('"prompt":"')
            if idx > -1:
                start = idx + len('"prompt":"')
                end = html_norm.find('","', start)
                if end > -1:
                    raw = _decode_json_string_chunk(
                        html_norm[start:end]
                    ).strip()
                    if len(raw) > 50 and raw.count("\n") > 3:
                        lyrics = raw
    except Exception as exc:
        # Non-fatal — we'll still try CDN URL construction below if we have a UUID
        print(f"[booth] suno page fetch failed: {exc}")

    if not song_uuid:
        return jsonify({"error": "Could not extract song UUID"}), 400

    embed_url = f"https://suno.com/embed/{song_uuid}"
    if not audio_url:
        audio_url = f"https://cdn1.suno.ai/{song_uuid}.mp3"
    if not image_url:
        image_url = f"https://cdn2.suno.ai/image_large_{song_uuid}.jpeg"

    result = {
        "embed_url": embed_url,
        "audio_url": audio_url,
        "image_url": image_url,
        "video_url": video_url,
        "uuid":      song_uuid,
        "title":     title,
        "lyrics":    lyrics,
        "duration":  duration,
    }
    _suno_cache[url] = result
    return jsonify(result)


@app.route("/api/announce", methods=["POST"])
@require_auth
def booth_announce() -> Response:
    """Post a 'Now Playing' message to a Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        return jsonify({"error": "No webhook configured"}), 400
    data = request.get_json() or {}
    title    = data.get("title", "")
    url      = data.get("url", "")
    platform = data.get("platform", "")

    display = title if title else platform
    message = f"**Now Playing:** {display}\n{url}"
    payload = json.dumps({"content": message, "username": "DJ Booth"}).encode()

    req = urllib.request.Request(DISCORD_WEBHOOK_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent",   DISCORD_USER_AGENT)
    try:
        urllib.request.urlopen(req, timeout=6)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/note")
@require_auth
def booth_get_note() -> Response:
    """Read the current dashboard note for a guild (or the most recent one)."""
    guild_id = request.args.get("guild")
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            if guild_id:
                cur.execute(
                    "SELECT message FROM dashboard_notes WHERE guild_id = %s",
                    (guild_id,),
                )
            else:
                cur.execute(
                    "SELECT message FROM dashboard_notes ORDER BY created_at DESC LIMIT 1"
                )
            row = cur.fetchone()
        return jsonify({"note": row["message"] if row else ""})
    except Exception:
        return jsonify({"note": ""})
    finally:
        conn.close()


@app.route("/api/note", methods=["POST"])
@require_auth
def booth_set_note() -> Response:
    """Set or clear the dashboard note for a guild."""
    data = request.get_json() or {}
    message  = (data.get("message") or "").strip()
    guild_id = data.get("guild_id", 0)
    conn = _booth_db()
    try:
        with conn.cursor() as cur:
            if message:
                cur.execute(
                    """
                    INSERT INTO dashboard_notes (guild_id, message)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE message    = VALUES(message),
                                            created_at = CURRENT_TIMESTAMP
                    """,
                    (guild_id, message),
                )
            else:
                cur.execute(
                    "DELETE FROM dashboard_notes WHERE guild_id = %s", (guild_id,)
                )
        conn.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


# ◸──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◹
#       SECTION: Entry point — bind to localhost only
# ◺──────── ♪ ──────── ◇ ─────── 📻 ─────── ◇ ──────── ♪ ────────◿

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
