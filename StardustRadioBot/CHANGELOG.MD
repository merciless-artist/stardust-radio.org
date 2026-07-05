# ╔════ ♱ · 𓆩⁜ ✨ ⁜𓆪 · ♱  ════╗
# ‖   Stardust Radio Bot — CHANGELOG
# ╚════ ♱ · 𓆩⁜ ✨ ⁜𓆪 · ♱  ════╝

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
