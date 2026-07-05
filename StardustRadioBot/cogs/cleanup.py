# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Cleanup — purge messages / clear tracked links
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""Quick moderator cleanup commands for LP hosts."""

import discord
from discord import app_commands
from discord.ext import commands


class Cleanup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="purge", description="Delete the last N messages in this channel")
    @app_commands.describe(count="How many recent messages to delete (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=count)
        await interaction.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

    @app_commands.command(
        name="clearlinks",
        description="Clear this channel's tracked LP links (keeps the party open)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def clearlinks(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.execute(
            "DELETE FROM tracked_links WHERE guild_id = %s AND channel_id = %s",
            (interaction.guild_id, interaction.channel_id),
        )
        await interaction.followup.send(
            "Cleared this channel's tracked links.", ephemeral=True
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You don't have permission to use that."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Cleanup(bot))
