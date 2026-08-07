"""
Stardust Radio Bot - lean radio + LP bot for the Stardust dashboard.

Run: python -u app.py STARDUST_RADIO_BOT
(The marker arg is used by the autostart launcher's single-instance guard.)
"""

import os
import sys
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.database import Database

# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Force UTF-8 for console I/O (fixes Windows cp1252
#                'charmap' UnicodeEncodeError on emoji / non-Latin
#                channel names, song titles, etc. in prints and logs)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    # Fallback for exotic environments where reconfigure isn't available.
    pass

load_dotenv()


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Bot configuration + intents
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

COGS = [
    "cogs.link_tracker",
    "cogs.radio_submit",
    "cogs.dj_stations",
    "cogs.voice",
    "cogs.cleanup",
    "cogs.support",
    "cogs.presence",
]


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: !stardust - help (help command is the bot name, house rule)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

@bot.command(name="stardust")
async def stardust_help(ctx: commands.Context):
    await ctx.send(
        "・♪ ──────── Stardust Radio Bot ──────── ♪・\n"
        "・/radio play ・ play the station in your voice channel\n"
        "・/radio stop ・ stop and leave\n"
        "・/radiosubmit ・ submit a song to the radio\n"
        "・/addsong ・ (DJs) add a song to your station or the community radio\n"
        "・!starttracking ・ start an LP in this channel (feeds the dashboard)\n"
        "・!stoptracking ・ end the LP and get the link list\n"
        "・/purge ・ delete recent messages\n"
        "・/clearlinks ・ clear this channel's tracked links\n"
        "・/support ・ support server + dashboard links"
    )


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: on_ready - DB, cogs, slash sync
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

@bot.event
async def on_ready():
    # Guard so a reconnect doesn't double-initialize the DB or re-load cogs.
    if not getattr(bot, "db", None):
        db = Database()
        await db.initialize()
        bot.db = db

        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"Loaded: {cog}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to load {cog}: {exc}", flush=True)

        try:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} slash commands", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Slash sync failed: {exc}", flush=True)

    print(f"Stardust Radio Bot online as {bot.user}", flush=True)


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Run with retry (exponential backoff)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

async def run_with_retry(token: str) -> None:
    delay, cap = 60, 3600
    while True:
        try:
            await bot.start(token)
        except discord.errors.HTTPException as exc:
            print(f"[WARN] HTTP {exc.status}; retry in {delay}s", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, cap)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR] {type(exc).__name__}: {exc}; retry in {delay}s", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, cap)
        else:
            delay = 60


if __name__ == "__main__":
    token = os.getenv("TOKEN")
    if not token:
        print("Error: TOKEN not found in .env", flush=True)
        sys.exit(1)
    try:
        asyncio.run(run_with_retry(token))
    except KeyboardInterrupt:
        print("[INFO] shutdown", flush=True)
