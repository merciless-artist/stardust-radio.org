# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Voice — play any Stardust Radio channel in a voice channel
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""Join a voice channel and stream any live Stardust Radio channel.

Stations are pulled dynamically from AzuraCast's public `/api/stations`
endpoint, so adding a new channel over there needs no bot code change —
it shows up in `/radio play` autocomplete on next refresh.
"""

import os
import time
import traceback
from typing import List, Optional

import httpx
import discord
from discord import app_commands
from discord.ext import commands


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Config
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

AZURACAST_BASE: str = os.getenv(
    "AZURACAST_BASE", "https://radio.stardust-radio.org"
).rstrip("/")

# Default station played when /radio play is invoked with no argument.
DEFAULT_STATION: str = os.getenv("AZURACAST_DEFAULT_STATION_SHORTCODE", "main")

# Reconnect at the FFmpeg layer so a dropped live stream resumes on its own
# without the bot leaving the channel.
_FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_FFMPEG_OPTS = "-vn"


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Station list (cached from AzuraCast /api/stations)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

# Cache the list for 60 s so autocomplete stays snappy but a newly-added
# station is discovered within about a minute.
_STATIONS_CACHE: dict = {"fetched_at": 0.0, "stations": []}
_STATIONS_TTL_SECONDS = 60


async def _fetch_stations() -> List[dict]:
    """Return a list of {shortcode, name} for every public AzuraCast station.

    Falls back to whatever we last saw if the API call fails so autocomplete
    keeps working even when AzuraCast is briefly unreachable.
    """
    now = time.time()
    if (
        _STATIONS_CACHE["stations"]
        and (now - _STATIONS_CACHE["fetched_at"]) < _STATIONS_TTL_SECONDS
    ):
        return _STATIONS_CACHE["stations"]

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{AZURACAST_BASE}/api/stations")
            resp.raise_for_status()
            raw = resp.json()
        stations = [
            {"shortcode": s["shortcode"], "name": s["name"]}
            for s in raw
            if s.get("is_public") and s.get("shortcode")
        ]
        # Keep the default station first if it exists; else alphabetical.
        stations.sort(key=lambda s: (s["shortcode"] != DEFAULT_STATION, s["name"].lower()))
        _STATIONS_CACHE["stations"] = stations
        _STATIONS_CACHE["fetched_at"] = now
    except Exception as exc:
        print(f"[voice] station list fetch failed: {exc}", flush=True)
        # Return stale cache (possibly empty) so callers don't crash.
    return _STATIONS_CACHE["stations"]


def _stream_url(shortcode: str) -> str:
    return f"{AZURACAST_BASE}/listen/{shortcode}/radio.mp3"


def _make_source(shortcode: str) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(
        _stream_url(shortcode),
        before_options=_FFMPEG_BEFORE,
        options=_FFMPEG_OPTS,
    )


async def _station_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Suggest station shortcodes based on what the user has typed so far."""
    stations = await _fetch_stations()
    q = (current or "").lower()
    matches = [
        app_commands.Choice(name=f"{s['name']} ({s['shortcode']})", value=s["shortcode"])
        for s in stations
        if q in s["shortcode"].lower() or q in s["name"].lower()
    ]
    # Discord caps autocomplete at 25 choices.
    return matches[:25]


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Voice cog
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

class Voice(commands.Cog):
    """Play any Stardust Radio channel in a voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    radio = app_commands.Group(
        name="radio",
        description="Play any Stardust Radio channel in voice",
        guild_only=True,
    )

    @radio.command(
        name="play",
        description="Join your voice channel and play a Stardust Radio channel",
    )
    @app_commands.describe(
        station=(
            "Which Stardust Radio channel to play — start typing to search. "
            "Leave blank for the main station."
        ),
    )
    @app_commands.autocomplete(station=_station_autocomplete)
    async def play(
        self,
        interaction: discord.Interaction,
        station: Optional[str] = None,
    ):
        member = interaction.user
        if (
            not isinstance(member, discord.Member)
            or not member.voice
            or not member.voice.channel
        ):
            await interaction.response.send_message(
                "Join a voice channel first, then run /radio play.", ephemeral=True
            )
            return

        shortcode = (station or DEFAULT_STATION).strip().lower()

        # Validate the shortcode against the live station list. Skip validation
        # if the station list came back empty (AzuraCast unreachable) — we still
        # try to play, in case the URL happens to be valid.
        stations = await _fetch_stations()
        if stations and not any(s["shortcode"] == shortcode for s in stations):
            valid = ", ".join(s["shortcode"] for s in stations) or "(none available)"
            await interaction.response.send_message(
                f"No Stardust Radio channel called `{shortcode}`.\n"
                f"Try one of: {valid}",
                ephemeral=True,
            )
            return

        display_name = next(
            (s["name"] for s in stations if s["shortcode"] == shortcode),
            shortcode,
        )

        voice_channel = member.voice.channel
        await interaction.response.defer(thinking=True)
        try:
            vc = interaction.guild.voice_client
            if vc is None:
                print(f"[voice] connecting to '{voice_channel.name}' "
                      f"({voice_channel.id}) for station '{shortcode}'", flush=True)
                vc = await voice_channel.connect(timeout=20.0, reconnect=True)
            elif vc.channel.id != voice_channel.id:
                print(f"[voice] moving to '{voice_channel.name}'", flush=True)
                await vc.move_to(voice_channel)

            if vc.is_playing():
                vc.stop()
            vc.play(_make_source(shortcode))

            await interaction.followup.send(
                f"Now playing **{display_name}** in {voice_channel.mention}. ♪"
            )
        except Exception as exc:  # noqa: BLE001 - surface connect/ffmpeg failures to the host
            print(f"[voice] play FAILED (station={shortcode}): "
                  f"{type(exc).__name__}: {exc}", flush=True)
            traceback.print_exc()
            await interaction.followup.send(f"Couldn't start playback: {exc}")

    @radio.command(
        name="stop",
        description="Stop the radio and leave the voice channel",
    )
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc is None:
            await interaction.response.send_message(
                "I'm not in a voice channel.", ephemeral=True
            )
            return
        await vc.disconnect(force=True)
        await interaction.response.send_message("Left the voice channel. ♪")

    @radio.command(
        name="channels",
        description="List every Stardust Radio channel available to play",
    )
    async def channels(self, interaction: discord.Interaction):
        stations = await _fetch_stations()
        if not stations:
            await interaction.response.send_message(
                "Couldn't reach AzuraCast to list channels — try again in a bit.",
                ephemeral=True,
            )
            return
        lines = ["・♪ ──────── Stardust Radio channels ──────── ♪・"]
        for s in stations:
            marker = " (default)" if s["shortcode"] == DEFAULT_STATION else ""
            lines.append(f"• **{s['name']}** — `{s['shortcode']}`{marker}")
        lines.append("\nUse `/radio play station:<shortcode>` to pick one.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    # Auto-leave-when-alone is intentionally disabled — the radio should
    # stay playing 24/7 in whatever channel the host asked for, regardless
    # of listener count. Use `/radio stop` to leave.


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
