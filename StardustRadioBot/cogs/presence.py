# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Presence — bot shows "Listening to [song] · Stardust Radio"
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""
Keeps the bot's Discord status in sync with what's playing on Stardust Radio,
so the whole server sees "Listening to [current song] · Stardust Radio" under
the bot — no install needed by anyone. Polls the AzuraCast now-playing feed.
"""

import os

import discord
import httpx
from discord.ext import commands, tasks

BASE = os.getenv("AZURACAST_BASE", "https://radio.stardust-radio.org").rstrip("/")
SHORTCODE = os.getenv("AZURACAST_STATION_SHORTCODE", "main")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class Presence(commands.Cog):
    """Mirror the station's now-playing into the bot's Discord status."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last = None
        self.update_presence.start()

    def cog_unload(self):
        self.update_presence.cancel()

    async def _now_playing(self) -> str:
        """Best-effort 'Song · Stardust Radio' from the AzuraCast feed."""
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _UA}) as c:
                data = (await c.get(f"{BASE}/api/nowplaying/{SHORTCODE}")).json()
            if isinstance(data, list):
                data = data[0]
            song = data.get("now_playing", {}).get("song", {})
            label = (song.get("title") or song.get("text") or "").strip()
            return f"{label} · Stardust Radio" if label else "Stardust Radio"
        except Exception:
            return "Stardust Radio"

    @tasks.loop(seconds=45)
    async def update_presence(self):
        name = (await self._now_playing())[:128]
        if name == self._last:
            return  # skip redundant updates (avoids needless gateway traffic)
        self._last = name
        try:
            await self.bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name=name)
            )
        except Exception:
            pass

    @update_presence.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Presence(bot))
