# Stardust Radio Dashboard

A self-hosted **listener site + DJ hosting console** built on top of AzuraCast.
Runs as a single Flask app behind a Cloudflare Tunnel — no port forwarding,
no exposed home IP.

Two independent surfaces on the same server:

- **Public listener side** — a multi-channel radio site. Anyone hits
  `stardust-radio.org`, picks a channel, and streams AzuraCast at full quality
  in a themed player.
- **Host side ("the Booth")** — a private hosting console for people running
  listening parties. Discord-OAuth-gated. Everything a host needs on one
  screen: queue, controls, listener count, away/screensaver mode.

---

## What it does

### For listeners
- Multi-channel picker (`/`) → per-channel listener page (`/<shortcode>`)
- Custom audio player with animated silver→gold state, listener count,
  recent-tracks log, volume slider
- Per-station theming: each AzuraCast station can ship its own art, colors,
  spotlight background, and video screensaver via a `theme.json`
- Track title + artist auto-refresh from AzuraCast's now-playing API
- Optional Twitch stage — a station can embed a Twitch stream instead of
  the AzuraCast player by setting `source_type: "twitch"` in its theme
- Optional Suno lyrics scraping — enriches the now-playing display when
  the track is on Suno

### For hosts (the Booth)
- **Discord OAuth sign-in** — only members of the support Discord can log in;
  optionally restricted further by role
- **Single-screen layout** — big buttons, no scrolling; everything a host
  needs is in one viewport
- **Left-side queue** with drag-to-reorder, auto-play, and manual add
- **Dual hosting** — two hosts can host the same session at once; state
  syncs across both booths every couple seconds
- **Autoplay + Away mode** — for solo hosts stepping away (bathroom, food);
  the queue advances on its own without dead air
- **Screensaver / away visuals** — short looping videos or an away card while
  the host is AFK
- **Delete-by-emoji** — every queued song is tagged with an emoji marker;
  hosts and submitters can remove songs by that marker
- **Pinned outro** — the LP always closes with the outro track you set
  (customizable outro pinning is on the roadmap)
- **Setlist retention** — songs stay after tracking stops so the host keeps
  the set list; "clear played" buttons handle cleanup
- **Multiple hosting modes:**
  - **Bot mode** — songs auto-flow into the queue when the Stardust Bot is
    in the LP's Discord channel and users drop links there (Suno,
    Producer.ai, YouTube, SoundCloud, ElevenLabs, or direct video uploads)
  - **No-server mode** — hosts can run a party without any Discord bot at
    all; sessions are stored locally as JSON, per-host
  - **Watcher mode** — a small local script (`watcher/dj-watcher.py`) reads
    Suno links out of a running Discord window and feeds them to the
    host's session over an authenticated token API

### Admin / integration
- REST-ish JSON API under `/api/` for queue management, solo-mode CRUD,
  stations list, theme lookup, DJ playback control
- Announce webhook — `POST /api/announce` fires a message into a configured
  Discord webhook (used by the bot for "now playing" pings)
- Guild-icon uploads for the booth picker
- Runs behind a session cookie (`FLASK_SECRET_KEY`); no listener accounts

---

## Architecture

```
┌────────── Listener's browser ──────────┐
│  [ Player ]   [ Discord chat embed ]   │
└───────────────┬────────────────────────┘
                │ https://yourdomain.com
    ┌───────────▼───────────┐
    │   Cloudflare Edge     │   DNS + Tunnel + free TLS
    └───────────┬───────────┘
                │  encrypted outbound tunnel
    ┌───────────▼───────────┐
    │  Host machine         │
    │  ├─ cloudflared       │
    │  ├─ Flask :8080  ──►  dashboard (this repo)
    │  └─ AzuraCast :80 ──►  stream + nowplaying API
    └───────────────────────┘
```

Cloudflare terminates TLS at its edge and forwards plain HTTP to the origin.
AzuraCast lives on `radio.yourdomain.com`, the dashboard on `yourdomain.com`.

---

## Requirements

- Python **3.10+**
- **MySQL / MariaDB** (any modern version)
- **AzuraCast** (Docker; WSL2 backend on Windows) — the actual streaming engine
- **Cloudflare** account with your domain routed through it, plus `cloudflared`
- A **Discord application** — needed for host OAuth sign-in. If you're only
  running the public listener side, you can skip this.

---

## File layout

```
Dashboard/
├── server.py                 # main Flask app — all routes (~45)
├── no_server_store.py        # solo-session storage backend
├── watcher_tokens.py         # watcher auth-token store
├── requirements.txt          # runtime deps
├── requirements-dev.txt      # test / lint deps
├── start_dashboard.bat       # Windows one-click launcher (creates venv)
├── .env.example
│
├── static/
│   ├── landing.html          # public entry — "Tune In" / "Host sign in"
│   ├── picker.html           # public channel picker
│   ├── listener.html         # public per-channel player
│   ├── index.html            # legacy single-channel entry
│   ├── booth-login.html      # host sign-in (Discord OAuth)
│   ├── booth-picker.html     # host: pick a server to host
│   ├── booth.html            # host: full hosting console
│   ├── css/                  # design tokens + per-page layout
│   ├── js/                   # listener player, landing, picker
│   ├── assets/               # UI art (buttons, frames, stages, icons)
│   └── themes/               # per-station overrides — see themes/_default
│
├── watcher/
│   ├── dj-watcher.py         # local Suno-link scraper for hosts
│   ├── start-watcher.bat
│   └── watcher-config.json
│
└── tests/                    # pytest suite
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in what you need. `.env.example` groups
them into sections; the short version:

| Group | Vars | Notes |
|---|---|---|
| Station identity | `STATION_NAME`, `ACCENT_COLOR` | Header + accent color |
| AzuraCast | `AZURACAST_BASE`, `AZURACAST_INTERNAL`, `AZURACAST_STATION_SHORTCODE` | Public base URL + internal URL for server-side calls + default station |
| Flask | `DASHBOARD_HOST`, `DASHBOARD_PORT`, `FLASK_SECRET_KEY` | Bind + session cookie secret |
| Discord OAuth (booth) | `DISCORD_OAUTH_CLIENT_ID`, `DISCORD_OAUTH_CLIENT_SECRET`, `DISCORD_OAUTH_REDIRECT_URI`, `DISCORD_GUILD_ID`, `DISCORD_DJ_ROLE_ID` | Only needed if you enable the host side |
| Discord bot / webhook | `DISCORD_BOT_TOKEN`, `DISCORD_WEBHOOK_URL`, `DJ_LOGO_PATH` | Optional integrations |
| MySQL (booth queue) | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` | Only used by the booth; listener side works without |

---

## Setup

Only stepping through this if you're setting up from scratch. Assumes you
already have your domain on Cloudflare and Docker Desktop installed
(with WSL2 on Windows).

### 1. AzuraCast

```bash
mkdir -p ~/azuracast && cd ~/azuracast
curl -fsSL https://raw.githubusercontent.com/AzuraCast/AzuraCast/main/docker.sh -o docker.sh
chmod +x docker.sh
./docker.sh install
```

Accept the default ports (80, 443, 8000-8500). Open `http://localhost`, create
an admin account, create at least one station, give it a `shortcode`, and
start it. Verify the stream in VLC at
`http://localhost/listen/<shortcode>/radio.mp3`.

### 2. Cloudflare Tunnel

```powershell
cloudflared tunnel login
cloudflared tunnel create stardust
```

Write `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: <UUID>
credentials-file: %USERPROFILE%\.cloudflared\<UUID>.json
ingress:
  - hostname: radio.yourdomain.com
    service: http://localhost:80
  - hostname: yourdomain.com
    service: http://localhost:8080
  - hostname: www.yourdomain.com
    service: http://localhost:8080
  - service: http_status:404
```

```powershell
cloudflared tunnel route dns stardust radio.yourdomain.com
cloudflared tunnel route dns stardust yourdomain.com
cloudflared tunnel route dns stardust www.yourdomain.com
cloudflared service install
```

### 3. Dashboard

```powershell
cd path\to\Dashboard
copy .env.example .env
notepad .env      # fill in AzuraCast + Flask + (optional) Discord + MySQL
start_dashboard.bat
```

First launch creates a venv and installs dependencies. The app binds
`127.0.0.1:8080` — Cloudflare Tunnel reaches in via localhost.

For production, run under Task Scheduler (Windows) or a systemd unit
(Linux) so it survives reboots. See
[`../deploy/windows-task-scheduler.md`](../deploy/windows-task-scheduler.md).

---

## Verification checklist

- [ ] `radio.yourdomain.com` → AzuraCast login screen
- [ ] `yourdomain.com` → landing page renders, no console errors
- [ ] Click "Tune In" → channel picker lists every public AzuraCast station
- [ ] Click a channel → player loads, audio plays, listener count updates
- [ ] Now-playing title/artist refresh within ~10s of a station song change
- [ ] "Host sign in" → Discord OAuth flow → booth picker → booth loads
- [ ] Booth queue accepts drag-reorder, delete, mark-played
- [ ] Reboot → AzuraCast (Docker auto-start), tunnel (service), and the
      dashboard (Task Scheduler / systemd) all come back up unattended

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the no-server-mode storage layer, watcher token auth,
solo-session routes, and watcher API. Booth OAuth and AzuraCast integration
are exercised by hand against a live station.
