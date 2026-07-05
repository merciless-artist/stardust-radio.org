"""
Link Tracker - capture LP music links so they feed the Stardust dashboard.

Tracks Suno, Producer.ai, YouTube, SoundCloud, and ElevenLabs links posted in
a channel while a listening party is running (!starttracking .. !stoptracking).
Trimmed from the Utility Bot: no contest votes, no reactions voting, no config
manager. Plain text only, native Discord permission checks.
"""

import re
import random
from typing import Optional

import discord
from discord.ext import commands


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Supported platforms + URL matching
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

TRACKED_DOMAINS = [
    'suno.com',
    'suno.ai',
    'producer.ai',
    'youtube.com',
    'youtu.be',
    'soundcloud.com',
    'elevenlabs.io',
]

# Match tracked-domain URLs, allowing subdomain prefixes (e.g. cdn1.suno.ai/..).
URL_PATTERN = re.compile(
    r'https?://(?:[\w-]+\.)*(?:'
    + '|'.join(re.escape(d) for d in TRACKED_DOMAINS)
    + r')[^\s<>\[\]`\'"]*',
    re.IGNORECASE,
)


def _emoji_before_url(content: str, url: str) -> Optional[str]:
    """Return a Unicode emoji that immediately precedes a URL, or None.

    Walks backwards from the URL past whitespace, then over consecutive
    non-ASCII-text characters. Captures variation selectors, ZWJ sequences,
    and regional-indicator flags. Discord custom emoji (<:foo:123>) are
    intentionally NOT detected (the dashboard can't render their CDN images).
    """
    idx = content.find(url)
    if idx <= 0:
        return None

    end = idx
    while end > 0 and content[end - 1].isspace():
        end -= 1
    if end == 0:
        return None

    start = end
    while start > 0:
        c = content[start - 1]
        if c.isspace():
            break
        if c.isalnum() and ord(c) < 128:
            break
        if c in '<>':
            break
        start -= 1

    candidate = content[start:end]
    if not candidate:
        return None
    if any(ord(c) > 127 for c in candidate):
        return candidate
    return None


# Random per-song marker (the dashboard shows it beside each queued song when
# the poster didn't supply their own emoji prefix).
SONG_EMOJIS = [
    '🎸', '🎹', '🥁', '🎺', '🎻', '🎷', '🪗', '🪘', '🪕', '🎤',
    '🦋', '🔥', '💎', '⚡', '🌙', '☀️', '🌊', '🍀', '🦊', '🐉',
    '🎯', '🏆', '👑', '💫', '🌟', '🎪', '🎭', '🎨', '🧊', '🫧',
    '🍒', '🍭', '🌸', '🌺', '🍄', '🪐', '🗡️', '🛸', '🎃', '🦄',
    '🐝', '🦅', '🐬', '🦩', '🌵', '🍉', '🧿', '💀', '🤖', '👾',
]


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Link Tracker cog
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

class LinkTracker(commands.Cog):
    """Capture LP music links into the shared dashboard database."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self):
        """Ensure the tables exist (shared DB already has them from Utility)."""
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_links (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username VARCHAR(100) DEFAULT NULL,
                url TEXT NOT NULL,
                platform VARCHAR(50),
                emoji VARCHAR(64) DEFAULT NULL,
                played TINYINT(1) DEFAULT 0,
                tracked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild_channel (guild_id, channel_id)
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_channels (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                started_by BIGINT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_channel (guild_id, channel_id)
            )
            """
        )

    async def _pick_emoji(self, guild_id: int) -> str:
        """Pick a per-song emoji not already used by another song in this guild."""
        used = await self.db.fetchall_dict(
            "SELECT emoji FROM tracked_links WHERE guild_id = %s AND emoji IS NOT NULL",
            (guild_id,),
        )
        used_set = {row['emoji'] for row in used}
        available = [e for e in SONG_EMOJIS if e not in used_set]
        return random.choice(available) if available else random.choice(SONG_EMOJIS)

    def _get_platform(self, url: str) -> str:
        """Determine the platform from a URL."""
        u = url.lower()
        if 'suno.com' in u or 'suno.ai' in u:
            return 'Suno'
        if 'producer.ai' in u:
            return 'Producer.ai'
        if 'youtube.com' in u or 'youtu.be' in u:
            return 'YouTube'
        if 'soundcloud.com' in u:
            return 'SoundCloud'
        if 'elevenlabs.io' in u:
            return 'ElevenLabs'
        return 'Unknown'

    # ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
    #       SECTION: Link detection listener
    # ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Capture tracked links posted in an actively-tracked channel."""
        if message.author.bot or not message.guild:
            return

        is_tracking = await self.db.fetchone(
            "SELECT 1 FROM tracking_channels WHERE guild_id = %s AND channel_id = %s",
            (message.guild.id, message.channel.id),
        )
        if not is_tracking:
            return

        # One unplayed song per non-admin per channel: ignore extra drops until
        # their queued song has played. Admins can queue multiple.
        if not message.author.guild_permissions.administrator:
            has_pending = await self.db.fetchone(
                "SELECT 1 FROM tracked_links "
                "WHERE guild_id = %s AND channel_id = %s AND user_id = %s AND played = 0",
                (message.guild.id, message.channel.id, message.author.id),
            )
            if has_pending:
                return

        # Uploaded video attachments count as songs too.
        VIDEO_EXTENSIONS = ('.mp4', '.webm', '.mov', '.avi', '.mkv')
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in VIDEO_EXTENSIONS):
                existing = await self.db.fetchone(
                    "SELECT 1 FROM tracked_links WHERE guild_id = %s AND url = %s",
                    (message.guild.id, attachment.url),
                )
                if not existing:
                    emoji = await self._pick_emoji(message.guild.id)
                    await self.db.execute(
                        "INSERT INTO tracked_links "
                        "(guild_id, channel_id, user_id, username, url, platform, emoji) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (message.guild.id, message.channel.id, message.author.id,
                         message.author.display_name, attachment.url, 'Upload', emoji),
                    )

        full_urls = re.findall(
            r'https?://(?:[\w-]+\.)*(?:'
            + '|'.join(re.escape(d) for d in TRACKED_DOMAINS)
            + r')[^\s<>\[\]`\'"]*',
            message.content,
            re.IGNORECASE,
        )
        if not full_urls:
            return

        for url in full_urls:
            if '/playlist/' in url:
                continue
            existing = await self.db.fetchone(
                "SELECT 1 FROM tracked_links WHERE guild_id = %s AND url = %s",
                (message.guild.id, url),
            )
            if existing:
                continue

            platform = self._get_platform(url)
            user_emoji = _emoji_before_url(message.content, url)
            emoji = user_emoji or await self._pick_emoji(message.guild.id)

            await self.db.execute(
                "INSERT INTO tracked_links "
                "(guild_id, channel_id, user_id, username, url, platform, emoji) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (message.guild.id, message.channel.id, message.author.id,
                 message.author.display_name, url, platform, emoji),
            )

    # ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
    #       SECTION: Tracking control commands
    # ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

    @commands.hybrid_command(name="starttracking", description="Start tracking links in this channel")
    @commands.has_guild_permissions(manage_guild=True)
    async def start_tracking(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Start tracking music links in a channel for the dashboard."""
        target = channel or ctx.channel
        try:
            await self.db.execute(
                "INSERT INTO tracking_channels (guild_id, channel_id, started_by) "
                "VALUES (%s, %s, %s)",
                (ctx.guild.id, target.id, ctx.author.id),
            )
            await ctx.send(
                f"・♪ Now tracking links in {target.mention}\n"
                "I'll save Suno, Producer.ai, YouTube, SoundCloud, and ElevenLabs "
                "links posted here for the listening party.\n"
                "End it with !stoptracking and you'll get the full list, then it's wiped."
            )
        except Exception:
            await ctx.send(f"{target.mention} is already being tracked.")

    @commands.hybrid_command(name="stoptracking", description="Stop tracking links in this channel")
    @commands.has_guild_permissions(manage_guild=True)
    async def stop_tracking(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Stop tracking, dump the link list, then wipe it."""
        target = channel or ctx.channel

        tracking_removed = await self.db.execute(
            "DELETE FROM tracking_channels WHERE guild_id = %s AND channel_id = %s",
            (ctx.guild.id, target.id),
        )

        rows = await self.db.fetchall_dict(
            "SELECT url FROM tracked_links "
            "WHERE guild_id = %s AND channel_id = %s ORDER BY tracked_at ASC",
            (ctx.guild.id, target.id),
        )

        if tracking_removed == 0 and not rows:
            await ctx.send(f"{target.mention} is not being tracked.")
            return

        if not rows:
            await ctx.send(f"・♪ Stopped tracking links in {target.mention}.")
            await self._clear_now_playing(ctx.guild.id, target.id)
            return

        urls = [row["url"] for row in rows]
        MAX_CONTENT = 1900
        chunks: list[str] = []
        current = ""
        for url in urls:
            line = url + "\n"
            if len(current) + len(line) > MAX_CONTENT and current:
                chunks.append(current.rstrip("\n"))
                current = line
            else:
                current += line
        if current:
            chunks.append(current.rstrip("\n"))

        for chunk in chunks:
            await ctx.send(f"```\n{chunk}\n```")

        await self.db.execute(
            "DELETE FROM tracked_links WHERE guild_id = %s AND channel_id = %s",
            (ctx.guild.id, target.id),
        )
        await self._clear_now_playing(ctx.guild.id, target.id)

        await ctx.send(
            "・♪ Listening party over. All song links have been permanently removed."
        )

    async def _clear_now_playing(self, guild_id: int, channel_id: int) -> None:
        """Clear the dashboard's now-playing pointer for this session (best effort)."""
        try:
            await self.db.execute(
                "DELETE FROM now_playing WHERE guild_id = %s AND channel_id = %s",
                (guild_id, channel_id),
            )
        except Exception:
            pass  # now_playing may be keyed per-guild only on older schemas

    @commands.hybrid_command(name="trackingchannels", description="List channels being tracked")
    @commands.has_guild_permissions(manage_guild=True)
    async def tracking_channels(self, ctx: commands.Context):
        """List all channels currently being tracked in this server."""
        channels = await self.db.fetchall_dict(
            "SELECT channel_id FROM tracking_channels WHERE guild_id = %s",
            (ctx.guild.id,),
        )
        if not channels:
            await ctx.send("No channels are being tracked.")
            return
        lines = "\n".join(f"・<#{row['channel_id']}>" for row in channels)
        await ctx.send(f"・♪ Tracking:\n{lines}")


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkTracker(bot))
