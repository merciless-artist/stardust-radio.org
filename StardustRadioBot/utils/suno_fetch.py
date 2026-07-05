"""
Suno song fetcher and ID3 stripper.

Given a Suno song URL, this helper:
  1. Resolves /s/ short links to canonical /song/<uuid> URLs
  2. Fetches the page HTML and extracts the CDN mp3 URL + UUID
  3. Downloads the mp3 bytes from the CDN
  4. Strips ID3 tags (title, artist, album, comments, lyrics, album art)

Designed to be Discord-agnostic so it can be unit tested without a bot.
The dashboard's existing Suno resolver in dashboard.py was used as reference
for the regex patterns — same source, different transport (httpx vs urllib).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Optional

import httpx


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: URL patterns and constants
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Matches both suno.com/song/<uuid> and suno.com/s/<id>
_SUNO_URL_RE = re.compile(
    r"^https?://(?:www\.)?suno\.com/(?:song|s)/[A-Za-z0-9-]+/?",
    re.IGNORECASE,
)

_UUID_RE = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
    re.IGNORECASE,
)

_SONG_PATH_UUID_RE = re.compile(r"/song/([a-f0-9-]{36})", re.IGNORECASE)

# Allow an optional ?query string after .mp3 — Suno sometimes serves
# tokenized CDN URLs like .../UUID.mp3?Expires=...&Signature=...
_AUDIO_URL_JSON_RE = re.compile(
    r'"audio_url"\s*:\s*"(https?://[^"]+?\.mp3(?:\?[^"]*)?)"'
)
_CDN_MP3_RE = re.compile(
    r"https://cdn\d+\.suno\.ai/[a-f0-9-]{36}\.mp3(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)

# Discord caps regular file uploads at 25 MB for non-Nitro / non-boosted servers.
# Suno songs are ~3-5 MB typically; this is a safety ceiling.
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Public dataclass and exception
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

@dataclass(frozen=True)
class SunoSong:
    """A downloaded, anonymized Suno song ready to be saved or sent."""

    suno_uuid: str
    canonical_url: str
    audio_url: str
    mp3_bytes: bytes


class SunoFetchError(Exception):
    """Base exception with a user-facing message and diagnostic context."""

    def __init__(
        self,
        user_message: str,
        *,
        debug: str = "",
        audio_url: str = "",
        suno_uuid: str = "",
    ) -> None:
        super().__init__(debug or user_message)
        self.user_message = user_message
        self.debug = debug or user_message
        self.audio_url = audio_url
        self.suno_uuid = suno_uuid


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: URL validation
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

def is_suno_url(url: str) -> bool:
    """Return True if the URL looks like a public Suno song page."""
    if not url or not isinstance(url, str):
        return False
    return bool(_SUNO_URL_RE.match(url.strip()))


def uuid_from_url(url: str) -> str:
    """Extract a Suno song UUID from a /song/<uuid> URL without any network call.

    Returns an empty string for /s/ short links (which redirect at fetch time)
    or any URL that doesn't contain a UUID. Useful for partial-data paths
    where we want to record what we can even if the full fetch fails.
    """
    if not url:
        return ""
    match = _SONG_PATH_UUID_RE.search(url)
    return match.group(1) if match else ""


async def resolve_uuid(url: str) -> str:
    """Best-effort online UUID resolution for `/s/<id>` short links.

    First tries the cheap regex on the URL itself. If that fails (because the
    URL is a `/s/` short link or anything else without a `/song/<uuid>` path),
    it does a single GET, follows redirects, and looks for a UUID in either
    the final URL or the page body. Returns an empty string if all of that
    fails — the caller decides what to do without a UUID.
    """
    cheap = uuid_from_url(url)
    if cheap:
        return cheap
    if not url:
        return ""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        async with httpx.AsyncClient(headers=headers) as client:
            response = await client.get(url, follow_redirects=True, timeout=10.0)
    except httpx.HTTPError:
        return ""
    if response.status_code >= 400:
        return ""
    match = _SONG_PATH_UUID_RE.search(str(response.url))
    if match:
        return match.group(1).lower()
    body_match = _UUID_RE.search(response.text)
    if body_match:
        return body_match.group(0).lower()
    return ""


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Page fetch + UUID/audio extraction
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

async def _fetch_page(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """Fetch the Suno page and return (final_url_after_redirects, html)."""
    try:
        response = await client.get(url, follow_redirects=True, timeout=15.0)
    except httpx.HTTPError as exc:
        raise SunoFetchError(
            "Could not load that song. Is it set to public?",
            debug=f"page fetch failed: {exc!r}",
        ) from exc

    if response.status_code == 404:
        raise SunoFetchError(
            "Could not find that song on Suno. Double-check the link.",
            debug=f"404 from {url}",
        )
    if response.status_code >= 400:
        raise SunoFetchError(
            "Could not load that song. Is it set to public?",
            debug=f"status {response.status_code} from {url}",
        )

    return str(response.url), response.text


def _extract_uuid_and_audio(url: str, final_url: str, html: str) -> tuple[str, str]:
    """Pull the song UUID and CDN mp3 URL out of the page HTML."""
    uuid: Optional[str] = None

    # Try the original URL, then the post-redirect URL, then the page body.
    for source in (url, final_url):
        match = _SONG_PATH_UUID_RE.search(source)
        if match:
            uuid = match.group(1)
            break

    if not uuid:
        match = _UUID_RE.search(html)
        if match:
            uuid = match.group(0)

    if not uuid:
        raise SunoFetchError(
            "Couldn't read that Suno page. Try a direct /song/ link.",
            debug="no uuid in url, redirect, or html",
        )

    # Suno's Next.js payload double-escapes JSON quotes; normalize for regex.
    html_norm = html.replace('\\"', '"')

    audio_url = ""
    match = _AUDIO_URL_JSON_RE.search(html_norm)
    if match:
        audio_url = match.group(1)
    if not audio_url:
        match = _CDN_MP3_RE.search(html_norm)
        if match:
            audio_url = match.group(0)

    # Last-resort fallback: construct the canonical CDN URL from the UUID.
    if not audio_url:
        audio_url = f"https://cdn1.suno.ai/{uuid}.mp3"

    return uuid, audio_url


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: CDN download + ID3 strip
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

async def _download_mp3(
    client: httpx.AsyncClient, audio_url: str, suno_uuid: str = ""
) -> bytes:
    """Stream the mp3 from the CDN, capping size at MAX_DOWNLOAD_BYTES.

    Errors carry the audio_url and suno_uuid so the cog's exception handler
    can log exactly which CDN target was rejected and still record the
    submission in the database with the metadata we already have.

    Per-request Referer/Origin headers mimic the browser context Suno's CDN
    expects — without these the CDN returns 403 even for valid /UUID.mp3 URLs.
    """
    download_headers = {
        "Referer": "https://suno.com/",
        "Origin": "https://suno.com",
        "Accept": "*/*",
    }
    try:
        async with client.stream(
            "GET",
            audio_url,
            follow_redirects=True,
            timeout=30.0,
            headers=download_headers,
        ) as response:
            if response.status_code >= 400:
                raise SunoFetchError(
                    "Download failed. Try again in a minute.",
                    debug=f"cdn status {response.status_code}",
                    audio_url=audio_url,
                    suno_uuid=suno_uuid,
                )

            buffer = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                buffer.extend(chunk)
                if len(buffer) > MAX_DOWNLOAD_BYTES:
                    raise SunoFetchError(
                        "That song is too large to upload to Discord.",
                        debug=f"exceeded {MAX_DOWNLOAD_BYTES} bytes",
                        audio_url=audio_url,
                        suno_uuid=suno_uuid,
                    )
            return bytes(buffer)
    except httpx.HTTPError as exc:
        raise SunoFetchError(
            "Download failed. Try again in a minute.",
            debug=f"cdn fetch failed: {exc!r}",
            audio_url=audio_url,
            suno_uuid=suno_uuid,
        ) from exc


def _strip_id3(mp3_bytes: bytes) -> bytes:
    """Remove ID3v2 (front of file) and ID3v1 (last 128 bytes) tags.

    Suno mp3s carry both. Stripping both wipes title, artist, lyrics, AND
    embedded album art (which lives in an APIC frame inside the ID3v2
    block) in one shot. No dependencies — just slice off the known headers.

    ID3v2 layout:
        bytes 0..2   = b"ID3"
        bytes 3..4   = major.minor version
        byte  5      = flags
        bytes 6..9   = synchsafe size of the tag body (top bit of each
                       byte is always 0, so 28 bits of payload size)
    Total ID3v2 frame = 10 byte header + body size.

    ID3v1 layout:
        Last 128 bytes of the file, starting with b"TAG".

    Returns the original bytes unchanged if no recognizable tag header is
    present — that just means there was nothing identifying to strip.
    """
    data = mp3_bytes

    # ID3v2 — strip from the front.
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (
            (data[6] << 21)
            | (data[7] << 14)
            | (data[8] << 7)
            | data[9]
        )
        data = data[10 + size:]

    # ID3v1 — strip from the back.
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]

    return data


# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Public entry point
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿

async def fetch_and_anonymize(url: str) -> SunoSong:
    """Fetch a Suno song page, download the mp3, and strip identifying tags.

    Parameters
    ----------
    url : str
        A suno.com/song/<uuid> or suno.com/s/<id> URL.

    Returns
    -------
    SunoSong
        UUID, canonical /song/ URL, and tag-stripped mp3 bytes.

    Raises
    ------
    SunoFetchError
        On any failure — `.user_message` is safe to display to the user.
    """
    url = url.strip()
    if not is_suno_url(url):
        raise SunoFetchError(
            "That does not look like a Suno song link.",
            debug=f"invalid url: {url!r}",
        )

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
    async with httpx.AsyncClient(headers=headers) as client:
        final_url, html = await _fetch_page(client, url)
        uuid, audio_url = _extract_uuid_and_audio(url, final_url, html)
        mp3 = await _download_mp3(client, audio_url, suno_uuid=uuid)

    # Run the CPU-bound mutagen strip off the event loop.
    stripped = await asyncio.to_thread(_strip_id3, mp3)

    canonical = f"https://suno.com/song/{uuid}"
    return SunoSong(
        suno_uuid=uuid,
        canonical_url=canonical,
        audio_url=audio_url,
        mp3_bytes=stripped,
    )
