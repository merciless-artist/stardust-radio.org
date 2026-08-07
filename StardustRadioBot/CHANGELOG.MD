# ╔════ ♱ · 𓆩⁜ ✨ ⁜𓆪 · ♱  ════╗
# ‖   Stardust Radio Bot — CHANGELOG
# ╚════ ♱ · 𓆩⁜ ✨ ⁜𓆪 · ♱  ════╝

## 2026-07-09 — DJ stations (self-service per-station adds)
- New `cogs/dj_stations.py`: DJs can add songs to their own AzuraCast station
  (or the community radio) from Discord — no AzuraCast navigating.
- `/djstation add|remove|list` (admins + RADIO MOD) assigns Discord users to
  stations (station picker autocompleted from AzuraCast). Stored in new
  `dj_station_access` table.
- `/addsong <link> <where>` (station DJs + admins): `where` autocompletes to the
  user's stations + "Community Radio". Uploads to that station's "Discord
  Submissions" playlist (community -> "Listener Submissions"). No approval,
  real title/artist. Created a "Discord Submissions" playlist on stations
  4/13/15/16 (ids 36/37/38/39).
- Registered in both bots' cog lists.

## 2026-07-09 — Trusted submitters (skip review)
- New `/radiotrust add|remove|list` (admins + RADIO MOD role) manages a global
  trusted-submitter list in a new `radio_trusted_users` table.
- A trusted user's `/radiosubmit` skips review entirely: uploads to the station
  immediately, tells them it's live, and still posts an "Auto-Approved
  (Trusted)" record in the submissions channel for accountability. Consent
  click is still logged.
- Refactored the approve/upload core into a shared `_upload_approved` helper
  (used by both the Approve button and auto-approve) and a `_can_review`
  permission helper. Mirrored into the Utility Bot's copy (now identical).

## 2026-07-07 — Self-healing radio submissions
- `record_submission`: when the review-channel post fails (channel not
  visible, or no Send permission), the pending row now deletes itself and the
  user is told nothing was saved — so a failed post can never strand a
  "pending" submission that blocks resubmits. (This stranded rows twice in
  practice.) Mirrored into the Utility Bot's copy.

## 2026-07-05 — Codex review fixes
- `link_tracker`: include `position INT DEFAULT NULL` in the `tracked_links`
  CREATE so a fresh deployment always has the column the dashboard orders by
  (avoids a startup-order race where the queue read could fail).
- `radio_submit`: `/radiosubmit` now guards against DMs (returns early if no
  server context) so the consent click can't hit the NOT-NULL guild_id insert.
  Same guard added to the Utility Bot's copy.
- Skipped the double-approve race suggestion by request (extremely unlikely in
  practice).

## 2026-07-04 — Initial build (lean radio + LP bot)
- New standalone bot, trimmed sibling of the Utility Bot, writing to the same
  local MariaDB so its listening parties feed the same dashboard.
- Scaffold: `app.py` (entrypoint, cog loader, retry, marker arg
  `STARDUST_RADIO_BOT`), `.env` (TOKEN + shared DB/AzuraCast/stream config),
  `requirements.txt`, `utils/` (ported `database.py` with trimmed table
  creation + `suno_fetch.py`).
- Cogs:
  - `link_tracker` — trimmed port: `on_message` capture + `!starttracking` /
    `!stoptracking` / `!trackingchannels`. Plain text (no emojis), native
    permission checks (Manage Server), and clears `now_playing` on stop.
  - `radio_submit` — ported as-is (centralized to the support-server channel;
    includes the title/artist naming fix).
  - `voice` — `/radio play` / `/radio stop`, FFmpeg-layer reconnect, auto-leave
    when alone.
  - `cleanup` — `/purge`, `/clearlinks`.
  - `support` — `/support` (support server + dashboard links).
  - `!stardust` — help (minimal text; pretty PNG menu deferred).
- Boot verified: DB connects, all 5 cogs load, 8 commands sync, online as
  `stardust-radio.org#1720`, present in 3 servers.
- Durability: `autostart_stardustbot.ps1` (guarded launcher), Startup
  `StardustBot_Autostart.vbs` (logon start), and `StardustRadioBot` Task
  Scheduler watchdog (self-heal every 5 min).
- Tests: `tests/test_link_detection.py` (URL matching, platform mapping, emoji
  prefix) — 4 passing.
- Spec + plan in `DOCS/superpowers/`.
