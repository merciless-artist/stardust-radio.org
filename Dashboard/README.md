# Stardust Radio Dashboard

A custom listening-party page that streams your AzuraCast station and embeds your Discord chat side-by-side. Music goes through AzuraCast at full quality (no Discord voice compression); chat goes through Discord (where your community already lives) via [WidgetBot](https://widgetbot.io).

```
┌─────────── Listener's browser ───────────┐
│  [ Custom player ]    [ Discord chat ]  │
└──────────────────┬───────────────────────┘
                   │ https://yourdomain.com
        ┌──────────▼──────────┐
        │   Cloudflare Edge   │   (DNS + Tunnel + free TLS)
        └──────────┬──────────┘
                   │  encrypted outbound tunnel
        ┌──────────▼──────────┐
        │  Your laptop         │
        │  ├─ cloudflared      │
        │  ├─ Flask :8080  ──► dashboard
        │  └─ AzuraCast :80 ──► stream + nowplaying API
        └──────────────────────┘
```

No port forwarding. No exposed home IP. Free.

---

## What you need before you start

- The new domain (you already have it on Cloudflare ✅)
- A Discord server you own + a channel for the listening-party chat
- Docker Desktop (Windows) with WSL2 backend enabled
- Python 3.10+ on Windows
- A text editor for editing `.env`

---

## Phase 1 — AzuraCast on your laptop (Docker via WSL2)

AzuraCast is Docker-only and on Windows runs through WSL2.

1. **Install Docker Desktop** if you don't have it. During setup, leave the "Use WSL2 based engine" option checked. Open Docker Desktop → Settings → Resources → WSL Integration and enable your Ubuntu distro.

2. **Open a WSL Ubuntu terminal** and install AzuraCast:
   ```bash
   mkdir -p ~/azuracast && cd ~/azuracast
   curl -fsSL https://raw.githubusercontent.com/AzuraCast/AzuraCast/main/docker.sh -o docker.sh
   chmod +x docker.sh
   ./docker.sh install
   ```
   The installer will ask which ports to use. **Accept the defaults** (HTTP 80, HTTPS 443, radio 8000-8500). It pulls the images and starts the stack.

3. **First-run wizard**: open `http://localhost` in your browser. Create an admin account with a strong, unique password.

4. **Create your station**:
   - Add a station, give it a name (e.g. "Stardust Radio") and a **shortcode** (lowercase, no spaces — e.g. `stardust_radio`). You'll need this shortcode for `.env`.
   - Upload some music to the station's media library.
   - Create a playlist, add tracks, set it to "general rotation" + shuffle.
   - Click "Start Station" / "Reload" so it begins broadcasting.

5. **Verify locally**: open `http://localhost/listen/stardust_radio/radio.mp3` in VLC. You should hear your station.

---

## Phase 2 — Cloudflare Tunnel (the safe public exposure)

Since the domain is already on Cloudflare, this is the easy part.

1. **Install cloudflared** for Windows: download the `.msi` from <https://github.com/cloudflare/cloudflared/releases/latest> and run it. Open a fresh PowerShell so `cloudflared` is on PATH.

2. **Authenticate** to Cloudflare:
   ```powershell
   cloudflared tunnel login
   ```
   A browser opens — pick your domain and authorize. A cert is saved to `%USERPROFILE%\.cloudflared\cert.pem`.

3. **Create the tunnel**:
   ```powershell
   cloudflared tunnel create stardust
   ```
   This prints a UUID and writes `<UUID>.json` (the tunnel credentials) to `%USERPROFILE%\.cloudflared\`.

4. **Write the config**. Create `%USERPROFILE%\.cloudflared\config.yml` (replace `<UUID>` and `yourdomain.com`):
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

5. **Route DNS** (creates the CNAME records on Cloudflare automatically):
   ```powershell
   cloudflared tunnel route dns stardust radio.yourdomain.com
   cloudflared tunnel route dns stardust yourdomain.com
   cloudflared tunnel route dns stardust www.yourdomain.com
   ```

6. **Test it** in the foreground first:
   ```powershell
   cloudflared tunnel run stardust
   ```
   On your phone (turn off WiFi, use cellular — proves it's not local LAN), open `https://radio.yourdomain.com`. You should see the AzuraCast login.

7. **Install as a Windows service** so it survives reboots:
   ```powershell
   cloudflared service install
   ```

---

## Phase 3 — Run the dashboard

1. **Copy the env template** and fill it in:
   ```powershell
   cd path\to\Dashboard
   copy .env.example .env
   notepad .env
   ```
   Set at minimum:
   - `STATION_NAME` — display name in the header
   - `ACCENT_COLOR` — hex color (e.g. `#ff4dd2`, `#00e5ff`)
   - `AZURACAST_BASE` — full URL with protocol, e.g. `https://stardust-radio.org` (the JS concatenates this with `/listen/...`, so it must include `https://`)
   - `AZURACAST_STATION_SHORTCODE` — the shortcode from step 4 of Phase 1

2. **Add WidgetBot to your Discord server** at <https://widgetbot.io>, then go to its dashboard, pick the channel you want embedded, and copy the embed snippet it gives you (an `<iframe>` or `<script>`). Paste it into `static/index.html` at the comment block labeled `PASTE WIDGETBOT CODE HERE`. No JS needed — the surrounding panel sizes and rounds it for you.

3. **Launch**:
   ```powershell
   start_dashboard.bat
   ```
   First run creates a venv and installs Flask. Visit `http://127.0.0.1:8080` locally to confirm it renders.

4. **Once Cloudflare Tunnel is running**, the same dashboard is live at `https://yourdomain.com`.

---

## End-to-end verification checklist

Run through this on a phone with WiFi off (cellular only) so you're hitting the real public path, not your LAN:

- [ ] `https://radio.yourdomain.com` → AzuraCast login screen
- [ ] `https://yourdomain.com` → dashboard renders, no console errors
- [ ] Click play → audio plays
- [ ] Volume slider changes loudness; setting persists on reload
- [ ] Now-playing track + art update within ~10 s of changing the song in AzuraCast
- [ ] Listener count shows a number (`1` if you're the only one)
- [ ] Discord chat panel loads and shows your channel; typing in it appears in Discord
- [ ] Page stacks cleanly on mobile (player on top, chat below)
- [ ] Reboot the laptop → AzuraCast (Docker auto-start) and tunnel (Windows service) both come back up without manual intervention

---

## File map

```
Dashboard/
├── server.py              # Flask app, binds 127.0.0.1:8080
├── requirements.txt
├── start_dashboard.bat    # one-click launcher (creates venv + runs)
├── .env.example           # template — copy to .env and edit
├── .gitignore
└── static/
    ├── index.html         # single-page layout
    ├── css/styles.css     # design tokens at top — change to reskin
    ├── js/
    │   └── player.js      # custom audio controls + nowplaying poll
    └── assets/
        ├── favicon.svg
        └── placeholder-art.svg
```

---

## Roadmap (post-MVP)

When you're ready to outgrow WidgetBot, the chat panel is structured so it can be swapped in isolation — replace `static/js/chat.js` with a websocket client and add a `bridge.py` (Discord Gateway listener + webhook sender). The player code, the dashboard layout, and the AzuraCast/Cloudflare setup all stay the same.

Other natural next steps:
- A small Discord bot that auto-posts a "🎵 Now playing" embed in `#now-playing` whenever the song changes
- `/lp start`, `/lp now`, `/lp listeners` slash commands
- Putting `radio.yourdomain.com` (the AzuraCast admin) behind Cloudflare Access so only you can hit `/admin`
