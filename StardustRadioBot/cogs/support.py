# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Support — support server + dashboard links
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""Point people at the support server (needed for dashboard access) + the dash."""

import os

import discord
from discord import app_commands
from discord.ext import commands

SUPPORT_INVITE = os.getenv("SUPPORT_INVITE_URL", "https://discord.gg/BpsFdRkB7u")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://stardust-radio.org")


class Support(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="support", description="Stardust Radio support server + dashboard links"
    )
    async def support(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "・♪ ──────── Stardust Radio ──────── ♪・\n"
            f"・Support server: {SUPPORT_INVITE}\n"
            f"・Dashboard: {DASHBOARD_URL}\n"
            "Join the support server to host listening parties on the dashboard."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Support(bot))
