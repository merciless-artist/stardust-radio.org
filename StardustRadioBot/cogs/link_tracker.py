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


async def _resolve_short_link(url: str) -> str:
    """Expand SoundCloud short links (on.soundcloud.com/...) to the real track URL.

    SoundCloud's embed player returns 404 for short links — it can only play a
    real soundcloud.com/<artist>/<track> URL. Follow the redirect once here so
    the stored link is directly playable. Falls back to the original on any
    error, and strips tracking junk (?si=, utm_*) so dupes match.
    """
    if 'on.soundcloud.com/' not in url.lower():
        return url
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (StardustRadio LinkResolver)"},
        ) as c:
            r = await c.get(url)
            final = str(r.url)
        if 'soundcloud.com/' in final:
            # keep the path (and ?in= playlist context if present), drop tracking params
            from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
            parts = urlsplit(final)
            keep = [(k, v) for k, v in parse_qsl(parts.query) if k == 'in']
            resolved = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep), ''))
            print(f"[links] resolved short link -> {resolved}", flush=True)
            return resolved
        print(f"[links] short link did not land on soundcloud.com: {final}", flush=True)
    except Exception as exc:  # noqa: BLE001
        # Log it — a silent fallback here hid a real failure once.
        print(f"[links] short-link resolve FAILED for {url}: {type(exc).__name__}: {exc}", flush=True)
    return url


def _clean_url(url: str) -> str:
    """Strip trailing sentence punctuation / markdown that isn't part of a URL.

    The capture regex stops at whitespace, so a link that ends a sentence drags
    its punctuation along: "…/cool-song." or "…/cool-song," or "**…/song**".
    SoundCloud (and others) then get a broken address and can't play it.
    Legit query strings ("?si=abc&utm_source=x") are untouched.
    """
    url = url.rstrip('.,;:!?*_~)')
    while url.endswith(')') and url.count('(') < url.count(')'):
        url = url[:-1]
    return url


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


# Per-song marker (the dashboard shows it beside each queued song when the
# poster didn't supply their own emoji prefix). Restricted to a color palette
# so `/queue` and `!delete <emoji>` stay readable and pickable.
SONG_EMOJIS = [
    # pinks / reds
    '🩷', '🔴', '🟥', '❤️',
    # oranges
    '🟠', '🧡',
    # yellows
    '🟡', '🟨', '💛',
    # greens
    '🟢', '🟩', '💚',
    # blues (💙 is canonical; 🩵 is treated as an alias — see _EMOJI_ALIASES)
    '🔵', '🟦', '💙',
    # purples
    '🟣', '🟪', '💜',
    # browns
    '🟤', '🟫', '🤎',
    # blacks
    '⚫', '⬛', '🖤',
    # whites / greys
    '⚪', '⬜', '🤍', '🔘', '🔲', '🩶',
]

# Aliases: emojis a user might type that should match the canonical stored form.
# Keeps `!delete 🩵` working when the queued song was stored under 💙.
_EMOJI_ALIASES = {
    '🩵': '💙',   # light-blue heart shares a "blue" slot with the classic blue heart
}


def _normalize_emoji(emoji: Optional[str]) -> Optional[str]:
    """Return the canonical form of an emoji (alias-collapsed) or the input as-is."""
    if not emoji:
        return emoji
    return _EMOJI_ALIASES.get(emoji, emoji)


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
                position INT DEFAULT NULL,
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

        for raw_url in full_urls:
            # Find the emoji prefix using the RAW match (it's what's actually in
            # the message text), then store the cleaned URL.
            user_emoji = _normalize_emoji(_emoji_before_url(message.content, raw_url))
            url = await _resolve_short_link(_clean_url(raw_url))
            if '/playlist/' in url:
                continue
            # A link is only a "duplicate" if it's STILL QUEUED (unplayed) in
            # this channel. A song that already played — or was X'd off by the
            # host — has left the list, so re-dropping it should re-queue it.
            # (Previously any URL ever seen in the guild was blocked, so a
            # re-drop after removal silently did nothing.)
            existing = await self.db.fetchone(
                "SELECT 1 FROM tracked_links "
                "WHERE guild_id = %s AND channel_id = %s AND url = %s AND played = 0",
                (message.guild.id, message.channel.id, url),
            )
            if existing:
                continue

            platform = self._get_platform(url)
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

    # ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
    #       SECTION: Self-service queue commands (delete / queue)
    # ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

    @commands.hybrid_command(
        name="delete",
        description="Remove your queued song by its emoji (e.g. !delete 💙)",
    )
    @discord.app_commands.describe(
        emoji="The emoji shown next to your song in /queue (🩵 also works as 💙).",
    )
    async def delete_song(self, ctx: commands.Context, emoji: str):
        """Delete the caller's own unplayed song in this channel matching `emoji`."""
        if ctx.guild is None:
            await ctx.send("This command only works inside a server.")
            return

        wanted = _normalize_emoji((emoji or "").strip())
        if not wanted:
            await ctx.send("Give me an emoji, e.g. `!delete 💙`.")
            return

        row = await self.db.fetchone_dict(
            "SELECT id, url, emoji FROM tracked_links "
            "WHERE guild_id = %s AND channel_id = %s AND user_id = %s "
            "  AND emoji = %s AND played = 0 "
            "ORDER BY tracked_at ASC LIMIT 1",
            (ctx.guild.id, ctx.channel.id, ctx.author.id, wanted),
        )
        if not row:
            await ctx.send(
                f"You have no queued song in this channel marked {emoji}. "
                "Run `/queue` to see what's in the queue and its emoji."
            )
            return

        await self.db.execute(
            "DELETE FROM tracked_links WHERE id = %s",
            (row["id"],),
        )
        print(f"[link_tracker] deleted song id={row['id']} emoji={row['emoji']} "
              f"by user={ctx.author.id} in channel={ctx.channel.id}", flush=True)
        await ctx.send(f"・♪ Removed your {row['emoji']} song from the queue.")

    @commands.hybrid_command(
        name="queue",
        description="Show every song currently queued in this channel with its emoji.",
    )
    async def show_queue(self, ctx: commands.Context):
        """List all unplayed tracked songs in the current channel."""
        if ctx.guild is None:
            await ctx.send("This command only works inside a server.")
            return

        rows = await self.db.fetchall_dict(
            "SELECT emoji, username, url, platform FROM tracked_links "
            "WHERE guild_id = %s AND channel_id = %s AND played = 0 "
            "ORDER BY tracked_at ASC",
            (ctx.guild.id, ctx.channel.id),
        )
        if not rows:
            await ctx.send("The queue is empty in this channel.")
            return

        header = "・♪ ──────── Current queue ──────── ♪・"
        lines = [header]
        for r in rows:
            marker = r.get("emoji") or "🎵"
            who = r.get("username") or "?"
            url = r.get("url") or ""
            # Trim really long CDN URLs so the message stays readable
            shown = url if len(url) <= 90 else url[:87] + "…"
            lines.append(f"{marker}  {shown}  · *{who}*")
        lines.append("\nRemove yours with `!delete <emoji>`.")

        # Discord caps messages at 2000 chars — chunk if needed.
        MAX = 1900
        buf = ""
        for line in lines:
            if buf and len(buf) + len(line) + 1 > MAX:
                await ctx.send(buf.rstrip())
                buf = ""
            buf += line + "\n"
        if buf:
            await ctx.send(buf.rstrip())


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkTracker(bot))
