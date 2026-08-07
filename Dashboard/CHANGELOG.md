# Changelog

## 2026-08-06 — Discord Radio Activity (v1 build)
- New embedded Activity at `/activity`: plays the live station, shows now-playing
  + art + participants, shared "pass-the-aux" 5-station picker. Plain HTML/JS +
  vendored Embedded App SDK (`static/activity/`), no Node runtime.
- Backend routes: `/activity`, `/activity/config.js`, `POST /api/activity/token`
  (OAuth exchange, secret in .env), `GET|POST /api/activity/instance/<id>/station`
  (in-memory shared station). Tests in `tests/test_activity.py` (6 passing).
- Root `/` serves the Activity when Discord opens it (`?frame_id=` present),
  landing page otherwise — so the root URL mapping needs no path.
- Pending: Dev-Portal Activities enable + URL mappings + live in-Discord smoke.
## 2026-08 — Discord domain verification
- Added `/.well-known/discord` route serving the `dh=...` domain-ownership proof
  (plain text, exact, no trailing newline) so stardust-radio.org can be linked
  to the Discord app / Activity. Verifying the root domain also covers the
  radio.stardust-radio.org subdomain for Activity URL mappings.

## 2026-07-25 — Simple No-Server Mode
- No-Server Mode is now plainly reachable: a "No-Server Sessions" panel on the
  booth picker creates, opens, and deletes botless sessions. Host with no bot
  and no database at all, just paste song links into the queue.
- Renamed the booth's "COPY DROP-LINK INVITE" button to "SUPPORT SERVER".

## 2026-07-05 — Fix: direct Suno CDN mp3s wouldn't auto-advance
- Raw `cdn1.suno.ai/<uuid>.mp3` links classify as platform "Suno" but aren't
  share pages, so `extractSunoId` returned null and they fell through to
  `loadGenericEmbed` — an iframe with a 15-min (900s) dummy countdown and no
  end-detection. Result: auto-advance never fired and the timer looked like
  "one long song."
- Fix: `loadGenericEmbed` now detects a direct-audio URL (`isDirectAudioUrl`)
  and routes it to `loadDirectAudio` (real `<audio>` element with `ended` +
  near-end `timeupdate` advance). Catches any similar fall-through too.
- Also: `loadDirectAudio` now sets the countdown from the song's real duration
  on `loadedmetadata` (was only the 6-min hard fallback), so the timer reflects
  the actual song. Share links (`suno.com/song|s/...`) are unchanged.
## 2026-07-05 — Emoji-prefixed manual add (contest voting)
- The manual "add a link" box now accepts an emoji prefix, e.g.
  `🟥 https://cdn1.suno.ai/….mp3` — the song shows on the dash with that exact
  emoji instead of a random one, so a host can mirror a Discord contest's
  per-song voting emoji. No emoji typed = random as before.
- Browser peels the emoji off the front and sends `{url, emoji}`; server honors
  a supplied emoji (non-ASCII, capped 32 chars) via the shared `_chosen_emoji`
  helper. Applied to both the No-Server and bot-mode add-paths.
- Bulk paste: pasting a multi-line block (a whole contest of `emoji  link`
  lines) into the add box adds every line as its own track, in order. A
  single-line paste/type still behaves normally.
## 2026-06-30 — Login page copy fix (stale DJ-role text)
- Removed the outdated "you need the DJ role in StudioAI" hint and the
  "your roles in the StudioAI server" fineprint on /booth/login; now neutral
  and accurate (sign in with Discord; reads basic profile + server membership).
- When a non-member is bounced, the error banner now renders a clickable gold
  "Join the support server" button (discord.gg/BpsFdRkB7u) instead of an
  unclickable URL string. Added `.auth-join-btn` to booth-auth.css (versioned
  link to bust cache). XSS-safe: message stays textContent; button href is
  hardcoded, not taken from the query param.

## 2026-06-30 — Landing "Host sign in" is now a gold pill button
- Replaced the small "Hosts: sign in" text link with a full gold pill button
  matching the TUNE IN CTA (reuses `.landing-cta__primary`), labeled
  "HOST SIGN IN". (Superseded an interim bigger/dark-brown text tweak, which
  read blurry against the backdrop.)
- Versioned the landing.css link (`?v=20260702`) to bust Cloudflare's 4-hour
  browser cache.

## 2026-06-30 — Durable dashboard launcher + boot autostart
- Added `autostart_dashboard.ps1` (guarded, single-instance) and a Startup
  `Dashboard_Autostart.vbs` so the Flask dashboard runs detached and survives,
  and now auto-starts on logon. Fixes the dash going down when a foreground
  launch got reaped. Delete the Startup .vbs to disable autostart.
- Added a Task Scheduler watchdog `StardustDashboard` (runs the guarded
  launcher every 5 min) that self-heals the dashboard if the process ever dies,
  independent of any terminal session. Remove with:
  `schtasks /Delete /TN StardustDashboard /F`.

## 2026-06-29 — Open booth sign-in (drop DJ-role gate)
- Removed the DJ-role requirement for the host booth. Any member of the
  Discord server can now sign in and use it; the guild-membership check stays
  (non-members still get "join the Discord first"). Existing sessions unaffected.
- Moved the membership gate from StudioAI to the **Stardust Radio Dashboard**
  support server (guild 1520585563336343603). To use the booth you must be a
  member of that support server. Login error message updated to match.
- Refreshed the booth "drop-link invite" to the live support-server invite
  (discord.gg/BpsFdRkB7u; old one was dead) and added it to the login bounce
  message so non-members can join.


## 2026-06-19 — No-Server Mode (Phase 1)
- New self-contained "No-Server Mode": create named local sessions (name + icon),
  paste links, and run a listening party with no Discord bot and no MySQL.
- New `/api/solo/...` routes backed by `no_server_sessions.json` (no database).
  Storage is concurrency-safe (serialized load-modify-save) and atomic.
- The dashboard now boots even when MySQL is down (bot-mode endpoints surface
  their own errors only when used).
- `booth.html` routes all queue calls through a solo-aware helper; bot mode is
  unchanged. `/booth?solo=<id>` serves the booth in No-Server Mode.
- Prominent booth context header showing the server/session + current mode.
- "No-Server Sessions" section on the booth picker (create / icon / delete),
  which works even when the database is off.
- 22 automated tests, including a dead-MySQL-port test proving No-Server Mode
  needs no database.
