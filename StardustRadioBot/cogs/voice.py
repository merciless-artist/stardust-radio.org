# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Voice — play Stardust Radio in a voice channel
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""Join a voice channel and stream the live Stardust Radio station."""

import os

import discord
from discord import app_commands
from discord.ext import commands

STREAM_URL = os.getenv(
    "AZURACAST_STREAM_URL",
    "https://radio.stardust-radio.org/listen/main/radio.mp3",
)

# Reconnect at the FFmpeg layer so a dropped live stream resumes on its own
# without the bot leaving the channel.
_FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
_FFMPEG_OPTS = "-vn"


def _make_source() -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(
        STREAM_URL, before_options=_FFMPEG_BEFORE, options=_FFMPEG_OPTS
    )


class Voice(commands.Cog):
    """Play/stop the station in voice, and auto-leave when alone."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    radio = app_commands.Group(
        name="radio", description="Play Stardust Radio in voice", guild_only=True
    )

    @radio.command(name="play", description="Join your voice channel and play Stardust Radio")
    async def play(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "Join a voice channel first, then run /radio play.", ephemeral=True
            )
            return

        channel = member.voice.channel
        await interaction.response.defer(thinking=True)
        try:
            vc = interaction.guild.voice_client
            if vc is None:
                vc = await channel.connect()
            elif vc.channel.id != channel.id:
                await vc.move_to(channel)
            if vc.is_playing():
                vc.stop()
            vc.play(_make_source())
            await interaction.followup.send(
                f"Now playing Stardust Radio in {channel.mention}. ♪"
            )
        except Exception as exc:  # noqa: BLE001 - surface connect/ffmpeg failures to the host
            await interaction.followup.send(f"Couldn't start playback: {exc}")

    @radio.command(name="stop", description="Stop the radio and leave the voice channel")
    async def stop(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc is None:
            await interaction.response.send_message(
                "I'm not in a voice channel.", ephemeral=True
            )
            return
        await vc.disconnect(force=True)
        await interaction.response.send_message("Left the voice channel. ♪")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Auto-leave when the bot is left alone in its channel."""
        vc = member.guild.voice_client
        if vc is None or vc.channel is None:
            return
        if not any(not m.bot for m in vc.channel.members):
            await vc.disconnect(force=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
