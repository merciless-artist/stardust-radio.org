# Stardust Radio Bot

A lean Discord bot that other servers can add to run listening parties on the
Stardust Radio dashboard. Trimmed sibling of the Utility Bot — it does only the
radio + LP essentials:

- **/radio play** / **/radio stop** — play the Stardust Radio station live in a voice channel
- **/radiosubmit** — submit a Suno song to the radio (centralized approval in the support server)
- **!starttracking** / **!stoptracking** — run an LP in a channel; links feed the dashboard
- **/purge** / **/clearlinks** — quick cleanup
- **/support** — support server + dashboard links
- **!stardust** — help

It writes to the same local MariaDB as the Utility Bot and dashboard, so its
listening parties show up on the same dashboard.

## Run

```
python -u app.py STARDUST_RADIO_BOT
```

Requires a `.env` with `TOKEN` (bot token) plus the DB / AzuraCast / stream vars
(see `.env`). Needs Message Content + Server Members intents enabled in the
Discord Developer Portal, and FFmpeg on PATH for voice.
