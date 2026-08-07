# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: DJ Stations — self-service song adds per station
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
"""
Let assigned DJs add songs to their own AzuraCast station (or the community
radio) straight from Discord — no AzuraCast navigating required.

  /djstation add|remove|list   (admins + RADIO MOD) — who can post where
  /addsong <link> <where>       (DJs) — add a song to a station you manage,
                                 or to the community radio

DJ uploads land in each station's "Discord Submissions" playlist; community
adds land in "Listener Submissions" (same as /radiosubmit). No approval —
station owners are trusted. Real title/artist (not anonymized).
"""

import base64
import os
import traceback

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from utils import suno_fetch
from cogs.radio_submit import _suno_title  # reuse the Suno title scraper


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Config + AzuraCast helpers
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

AZURACAST_BASE   = os.getenv("AZURACAST_BASE", "https://radio.stardust-radio.org").rstrip("/")
AZURACAST_API_KEY = os.getenv("AZURACAST_API_KEY", "")
COMMUNITY_STATION_ID  = int(os.getenv("AZURACAST_STATION_ID", "14") or "14")
COMMUNITY_PLAYLIST_ID = int(os.getenv("AZURACAST_SUBMISSIONS_PLAYLIST_ID", "22") or "22")
APPROVER_ROLE_ID = int(os.getenv("RADIO_APPROVER_ROLE_ID", "0") or "0")
SUBMISSIONS_PLAYLIST_NAME = "Discord Submissions"

_H = {"X-API-Key": AZURACAST_API_KEY}
_stations_cache = None  # [(id, name), ...] — refreshed on restart


async def _api_get(path: str):
    async with httpx.AsyncClient(timeout=30, headers=_H) as c:
        r = await c.get(AZURACAST_BASE + path)
        r.raise_for_status()
        return r.json()


async def _stations():
    """AzuraCast stations as [(id, name), ...], cached per process."""
    global _stations_cache
    if _stations_cache is None:
        _stations_cache = [(s["id"], s["name"]) for s in await _api_get("/api/stations")]
    return _stations_cache


async def _submissions_playlist_id(station_id: int):
    """The 'Discord Submissions' playlist id on a station, or None."""
    for pl in await _api_get(f"/api/station/{station_id}/playlists"):
        if pl.get("name") == SUBMISSIONS_PLAYLIST_NAME:
            return pl["id"]
    return None


async def _upload(station_id: int, playlist_id: int, mp3_bytes: bytes,
                  filename: str, title, artist):
    """Upload mp3 bytes to a station + assign the playlist and metadata."""
    b64 = base64.b64encode(mp3_bytes).decode()
    async with httpx.AsyncClient(timeout=90, headers=_H) as c:
        up = await c.post(
            f"{AZURACAST_BASE}/api/station/{station_id}/files",
            json={"path": f"Discord Submissions/{filename}", "file": b64},
        )
        up.raise_for_status()
        media_id = up.json()["id"]
        body = {"playlists": [int(playlist_id)]}
        if title:
            body["title"] = title
        if artist:
            body["artist"] = artist
        assign = await c.put(
            f"{AZURACAST_BASE}/api/station/{station_id}/file/{media_id}",
            headers={"Content-Type": "application/json"}, json=body,
        )
        assign.raise_for_status()
    return media_id


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Cog
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

class DJStations(commands.Cog):
    """Per-station DJ access + self-service song adds."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    async def cog_load(self):
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS dj_station_access (
                station_id INT NOT NULL,
                station_name VARCHAR(100),
                user_id BIGINT NOT NULL,
                added_by BIGINT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (station_id, user_id)
            )
        """)

    def _is_admin(self, member) -> bool:
        """Admin or RADIO MOD — may manage station access + post anywhere."""
        perms = getattr(member, "guild_permissions", None)
        is_admin = bool(perms and perms.administrator)
        role_ids = [getattr(r, "id", None) for r in getattr(member, "roles", [])]
        has_role = bool(APPROVER_ROLE_ID) and APPROVER_ROLE_ID in role_ids
        return is_admin or has_role

    async def _user_stations(self, user_id: int):
        """[(station_id, station_name), ...] a user is assigned to."""
        rows = await self.db.fetchall_dict(
            "SELECT station_id, station_name FROM dj_station_access WHERE user_id = %s",
            (user_id,))
        return [(r["station_id"], r["station_name"]) for r in rows]

    # ── /djstation (admins) ───────────────────────────────────────────────────
    djstation = app_commands.Group(
        name="djstation",
        description="Assign DJs to stations (they can add songs from Discord)",
        guild_only=True,
    )

    async def _station_ac(self, interaction: discord.Interaction, current: str):
        try:
            stations = await _stations()
        except Exception:
            return []
        cur = current.lower()
        return [app_commands.Choice(name=n, value=str(i))
                for i, n in stations if cur in n.lower()][:25]

    @djstation.command(name="add", description="Let a user add songs to a station")
    @app_commands.describe(station="Which station", user="Who to give access")
    @app_commands.autocomplete(station=_station_ac)
    async def dj_add(self, interaction: discord.Interaction, station: str, user: discord.User):
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("Admins / RADIO MOD only.", ephemeral=True)
            return
        if not station.isdigit():
            await interaction.response.send_message("Pick a station from the list.", ephemeral=True)
            return
        sid = int(station)
        try:
            name = dict(await _stations()).get(sid, f"Station {sid}")
        except Exception:
            name = f"Station {sid}"
        await self.db.execute(
            "INSERT INTO dj_station_access (station_id, station_name, user_id, added_by) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE station_name = VALUES(station_name), added_by = VALUES(added_by)",
            (sid, name, user.id, interaction.user.id))
        await interaction.response.send_message(
            f"{user.mention} can now add songs to **{name}** from Discord with /addsong.")

    @djstation.command(name="remove", description="Remove a user's access to a station")
    @app_commands.describe(station="Which station", user="Who to remove")
    @app_commands.autocomplete(station=_station_ac)
    async def dj_remove(self, interaction: discord.Interaction, station: str, user: discord.User):
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("Admins / RADIO MOD only.", ephemeral=True)
            return
        sid = int(station) if station.isdigit() else -1
        removed = await self.db.execute(
            "DELETE FROM dj_station_access WHERE station_id = %s AND user_id = %s",
            (sid, user.id))
        if removed:
            await interaction.response.send_message(f"Removed {user.mention} from that station.")
        else:
            await interaction.response.send_message(
                f"{user.mention} didn't have access to that station.", ephemeral=True)

    @djstation.command(name="list", description="Show who can post to which station")
    async def dj_list(self, interaction: discord.Interaction):
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("Admins / RADIO MOD only.", ephemeral=True)
            return
        rows = await self.db.fetchall_dict(
            "SELECT station_name, user_id FROM dj_station_access ORDER BY station_name")
        if not rows:
            await interaction.response.send_message("No DJ station assignments yet.", ephemeral=True)
            return
        by_station: dict[str, list[int]] = {}
        for r in rows:
            by_station.setdefault(r["station_name"] or "?", []).append(r["user_id"])
        lines = [f"・**{name}**: " + ", ".join(f"<@{u}>" for u in uids)
                 for name, uids in by_station.items()]
        await interaction.response.send_message(
            "・♪ ──────── DJ Station Access ──────── ♪・\n" + "\n".join(lines), ephemeral=True)

    # ── /addsong (DJs) ────────────────────────────────────────────────────────
    async def _dest_ac(self, interaction: discord.Interaction, current: str):
        cur = current.lower()
        dests: list[tuple[str, str]] = []
        if self._is_admin(interaction.user):
            try:
                dests.extend((n, str(i)) for i, n in await _stations())
            except Exception:
                pass
        else:
            dests.extend((name, str(sid))
                         for sid, name in await self._user_stations(interaction.user.id))
        dests.append(("Community Radio", "community"))
        seen, out = set(), []
        for n, v in dests:
            if v in seen:
                continue
            seen.add(v)
            if cur in n.lower():
                out.append(app_commands.Choice(name=n, value=v))
        return out[:25]

    @app_commands.command(name="addsong",
                          description="Add a song to your station or the community radio")
    @app_commands.describe(link="Suno song link", where="Which station to add it to")
    @app_commands.autocomplete(where=_dest_ac)
    @app_commands.guild_only()
    async def addsong(self, interaction: discord.Interaction, link: str, where: str):
        is_admin = self._is_admin(interaction.user)
        my_stations = await self._user_stations(interaction.user.id)
        if not is_admin and not my_stations:
            await interaction.response.send_message(
                "You're not set up as a station DJ yet. Ask an admin to add you "
                "with /djstation add.", ephemeral=True)
            return
        if not suno_fetch.is_suno_url(link):
            await interaction.response.send_message(
                "That doesn't look like a Suno song link "
                "(needs a suno.com/song/... or suno.com/s/... URL).", ephemeral=True)
            return

        # Resolve destination -> (station_id, playlist_id, display name)
        if where == "community":
            station_id, playlist_id, dest_name = (
                COMMUNITY_STATION_ID, COMMUNITY_PLAYLIST_ID, "Community Radio")
        else:
            if not where.isdigit():
                await interaction.response.send_message(
                    "Pick a station from the list.", ephemeral=True)
                return
            station_id = int(where)
            if not (is_admin or any(sid == station_id for sid, _ in my_stations)):
                await interaction.response.send_message(
                    "You don't have access to that station.", ephemeral=True)
                return
            playlist_id = await _submissions_playlist_id(station_id)
            if playlist_id is None:
                await interaction.response.send_message(
                    "That station has no 'Discord Submissions' playlist — tell an admin.",
                    ephemeral=True)
                return
            dest_name = dict(await _stations()).get(station_id, f"Station {station_id}")

        await interaction.response.defer(thinking=True)
        try:
            song = await suno_fetch.fetch_and_anonymize(link)
            title = await _suno_title(link)
            await _upload(station_id, playlist_id, song.mp3_bytes,
                          f"dj_{song.suno_uuid}.mp3", title, interaction.user.display_name)
        except suno_fetch.SunoFetchError as e:
            await interaction.followup.send(f"Couldn't fetch that song: {e.user_message}")
            return
        except Exception as e:  # noqa: BLE001
            print(f"[djstation] addsong upload failed: {e!r}", flush=True)
            traceback.print_exc()
            await interaction.followup.send(f"Upload to the station failed: {e}")
            return

        await interaction.followup.send(
            f"・♪ Added to **{dest_name}** — it's on the station now.")


async def setup(bot: commands.Bot):
    await bot.add_cog(DJStations(bot))
