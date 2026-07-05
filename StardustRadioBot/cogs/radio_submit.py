"""
Radio submissions — let listeners submit a Suno song to Stardust Radio.

  /radiosubmit <suno link>

Flow:
  1. Listener runs /radiosubmit and clicks "I Agree & Submit" (the click is
     their consent; it's logged).
  2. The bot posts the submission to the mod channel with Approve / Reject
     buttons (admins only). The buttons survive bot restarts.
  3. On Approve, the bot downloads + anonymizes the song (utils.suno_fetch),
     uploads it to the Stardust-Radio AzuraCast station, and drops it into the
     "Listener Submissions" playlist. On Reject, it's discarded.

All AzuraCast/station config comes from .env so nothing sensitive is in code.
"""

import asyncio
import base64
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils import suno_fetch


# ◸──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◹
#       SECTION: Config (from .env)
# ◺──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◿

AZURACAST_BASE     = os.getenv("AZURACAST_BASE", "https://radio.stardust-radio.org").rstrip("/")
AZURACAST_API_KEY  = os.getenv("AZURACAST_API_KEY", "")
STATION_ID         = os.getenv("AZURACAST_STATION_ID", "")
SUBMISSIONS_PLAYLIST_ID = os.getenv("AZURACAST_SUBMISSIONS_PLAYLIST_ID", "")
SUBMIT_CHANNEL_ID  = int(os.getenv("RADIO_SUBMIT_CHANNEL_ID", "0") or "0")
APPROVER_ROLE_ID   = int(os.getenv("RADIO_APPROVER_ROLE_ID", "0") or "0")

AGREEMENT_TEXT = (
    "・♪ ──────── Radio Submission ──────── ♪・\n"
    "By submitting, you confirm you created or own the rights to this song "
    "and you grant Stardust Radio permission to broadcast it. Your submission "
    "and this agreement are logged. A host reviews every submission before it "
    "airs.\n\n"
    "Click \"I Agree & Submit\" to send it for review."
)


# ◸──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◹
#       SECTION: AzuraCast upload helper
# ◺──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◿

_SCRAPE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def _suno_title(url: str):
    """Best-effort: the song's title from its Suno page (the mp3 tags are
    stripped, so we set this on AzuraCast or it shows the raw filename)."""
    import re
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": _SCRAPE_UA}) as c:
            html = (await c.get(url, follow_redirects=True)).text
    except Exception:
        return None
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        t = m.group(1).strip()
        if t.endswith(" | Suno"):
            t = t[: -len(" | Suno")]
        return t or None
    return None


async def _azuracast_add(mp3_bytes: bytes, filename: str, title=None, artist=None) -> int:
    """Upload mp3 bytes to the station, set a readable title/artist, and add to
    the submissions playlist. Returns the AzuraCast media id. Raises on error."""
    import httpx
    headers = {"X-API-Key": AZURACAST_API_KEY}
    b64 = base64.b64encode(mp3_bytes).decode()
    body = {"playlists": [int(SUBMISSIONS_PLAYLIST_ID)]}
    if title:
        body["title"] = title
    if artist:
        body["artist"] = artist
    async with httpx.AsyncClient(timeout=90) as client:
        up = await client.post(
            f"{AZURACAST_BASE}/api/station/{STATION_ID}/files",
            headers=headers,
            json={"path": f"Submissions/{filename}", "file": b64},
        )
        up.raise_for_status()
        media_id = up.json()["id"]
        assign = await client.put(
            f"{AZURACAST_BASE}/api/station/{STATION_ID}/file/{media_id}",
            headers=headers,
            json=body,
        )
        assign.raise_for_status()
    return media_id


# ◸──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◹
#       SECTION: Buttons
# ◺──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◿

class AgreeView(discord.ui.View):
    """Shown to the submitter — clicking the button is their consent."""

    def __init__(self, link: str, user_id: int):
        super().__init__(timeout=300)
        self.link = link
        self.user_id = user_id

    @discord.ui.button(label="I Agree & Submit", style=discord.ButtonStyle.success)
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "That submission button isn't yours.", ephemeral=True)
            return
        # Acknowledge immediately so the click never shows "interaction failed",
        # even if the work below is slow or the review channel rejects us.
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass
        cog = interaction.client.get_cog("RadioSubmit")
        if cog:
            await cog.record_submission(interaction, self.link)


class DecisionButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"rsub:(?P<action>approve|reject):(?P<sid>\d+)"):
    """Approve/Reject buttons in the mod channel. Dynamic = survive restarts."""

    def __init__(self, action: str, sid: int):
        self.action = action
        self.sid = sid
        super().__init__(discord.ui.Button(
            style=discord.ButtonStyle.success if action == "approve" else discord.ButtonStyle.danger,
            label="Approve" if action == "approve" else "Reject",
            custom_id=f"rsub:{action}:{sid}",
        ))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["sid"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RadioSubmit")
        if cog:
            await cog.handle_decision(interaction, self.sid, self.action == "approve")


def _mod_view(sid: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DecisionButton("approve", sid))
    view.add_item(DecisionButton("reject", sid))
    return view


# ◸──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◹
#       SECTION: Cog
# ◺──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◿

class RadioSubmit(commands.Cog):
    """Listener radio submissions with host approval."""

    def __init__(self, bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS radio_submissions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                username VARCHAR(100),
                url TEXT NOT NULL,
                suno_uuid VARCHAR(64) DEFAULT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                agreed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decided_by BIGINT DEFAULT NULL,
                decided_at TIMESTAMP NULL,
                mod_message_id BIGINT DEFAULT NULL,
                INDEX idx_status (status)
            )
        """)
        # Re-bind the Approve/Reject buttons so they work after a restart.
        self.bot.add_dynamic_items(DecisionButton)

    # ── /radiosubmit ────────────────────────────────────────────────────────
    @commands.hybrid_command(name="radiosubmit",
                             description="Submit your Suno song to be played on Stardust Radio")
    async def radiosubmit(self, ctx: commands.Context, link: str):
        if not suno_fetch.is_suno_url(link):
            await ctx.send("That doesn't look like a Suno song link "
                           "(needs to be a suno.com/song/... or suno.com/s/... URL).")
            return
        # One pending submission per person at a time.
        pending = await self.db.fetchone_dict(
            "SELECT id FROM radio_submissions WHERE user_id = %s AND status = 'pending'",
            (ctx.author.id,))
        if pending:
            await ctx.send("You already have a submission waiting for review. "
                           "Hang tight until a host gets to it.")
            return
        await ctx.send(AGREEMENT_TEXT, view=AgreeView(link, ctx.author.id))

    # ── Record a consented submission + post it for review ────────────────────
    async def record_submission(self, interaction: discord.Interaction, link: str):
        sid = await self.db.execute(
            """INSERT INTO radio_submissions (guild_id, user_id, username, url, status)
               VALUES (%s, %s, %s, %s, 'pending')""",
            (interaction.guild_id, interaction.user.id,
             interaction.user.display_name, link),
        )

        channel = self.bot.get_channel(SUBMIT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(SUBMIT_CHANNEL_ID)
            except Exception:
                channel = None
        if channel is None:
            await interaction.followup.send(
                "Your song was saved, but I can't see the review channel — "
                "a host needs to add me to that server/channel.")
            return

        try:
            msg = await channel.send(
                "・♪ ──────── New Radio Submission ──────── ♪・\n"
                f"From: {interaction.user.mention}\n"
                f"Song: {link}\n"
                "Approve to add it to the Listener Submissions playlist.",
                view=_mod_view(sid),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Your song was saved, but I don't have permission to post in the "
                "review channel. Tell a host to give me Send Messages there.")
            return

        await self.db.execute(
            "UPDATE radio_submissions SET mod_message_id = %s WHERE id = %s",
            (msg.id, sid))

        await interaction.followup.send(
            "Submitted for review. If a host approves it, it'll play on Stardust Radio. Thanks!")

    # ── Approve / Reject (admins only) ───────────────────────────────────────
    async def handle_decision(self, interaction: discord.Interaction, sid: int, approve: bool):
        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        is_admin = bool(perms and perms.administrator)
        role_ids = [getattr(r, "id", None) for r in getattr(member, "roles", [])]
        has_role = bool(APPROVER_ROLE_ID) and APPROVER_ROLE_ID in role_ids
        print(f"[radiosub] decision sid={sid} approve={approve} by={member} "
              f"admin={is_admin} has_role={has_role} approver_role={APPROVER_ROLE_ID} "
              f"member_roles={role_ids}", flush=True)
        if not (is_admin or has_role):
            await interaction.response.send_message(
                "You don't have permission to review submissions.", ephemeral=True)
            return

        row = await self.db.fetchone_dict(
            "SELECT * FROM radio_submissions WHERE id = %s", (sid,))
        if not row:
            await interaction.response.send_message("That submission is gone.", ephemeral=True)
            return
        if row["status"] != "pending":
            await interaction.response.send_message(
                f"Already {row['status']}.", ephemeral=True)
            return

        await interaction.response.defer()
        decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if not approve:
            await self.db.execute(
                "UPDATE radio_submissions SET status='rejected', decided_by=%s, decided_at=%s WHERE id=%s",
                (interaction.user.id, decided_at, sid))
            await self._finish(interaction,
                               f"Rejected by {interaction.user.display_name}.")
            await self._dm(row["user_id"],
                           "・♪ ──────── Stardust Radio ──────── ♪・\n"
                           "Your song submission wasn't approved this time. "
                           "You're welcome to submit a different one anytime.\n"
                           f"Song: {row['url']}")
            return

        # Approve: download -> upload -> add to playlist.
        try:
            song = await suno_fetch.fetch_and_anonymize(row["url"])
            title = await _suno_title(row["url"])
            await _azuracast_add(song.mp3_bytes, f"sub_{song.suno_uuid}.mp3",
                                 title=title, artist=row.get("username"))
        except suno_fetch.SunoFetchError as e:
            print(f"[radiosub] fetch failed sid={sid}: {e.debug}", flush=True)
            await interaction.followup.send(
                f"Couldn't fetch that song: {e.user_message}", ephemeral=True)
            return
        except Exception as e:
            import traceback
            print(f"[radiosub] approve failed sid={sid}: {e!r}", flush=True)
            traceback.print_exc()
            await interaction.followup.send(
                f"Upload to the radio failed: {e}", ephemeral=True)
            return

        await self.db.execute(
            """UPDATE radio_submissions
               SET status='approved', suno_uuid=%s, decided_by=%s, decided_at=%s WHERE id=%s""",
            (song.suno_uuid, interaction.user.id, decided_at, sid))
        await self._finish(interaction,
                           f"Approved by {interaction.user.display_name} — now on Stardust Radio.")
        await self._dm(row["user_id"],
                       "・♪ ──────── Stardust Radio ──────── ♪・\n"
                       "Good news — your song was approved and is now on Stardust Radio. "
                       "Thanks for submitting!\n"
                       f"Song: {row['url']}")

    async def _dm(self, user_id: int, text: str):
        """Best-effort DM to the submitter. Silently ignores closed DMs."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                await user.send(text)
        except Exception:
            pass

    async def _finish(self, interaction: discord.Interaction, note: str):
        """Append the decision to the mod message and remove the buttons."""
        try:
            base = interaction.message.content.split("\nStatus:")[0]
            await interaction.message.edit(content=base + "\nStatus: " + note, view=None)
        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(RadioSubmit(bot))
