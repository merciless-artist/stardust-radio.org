"""Pure-logic tests for the link tracker's URL detection + platform mapping."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.link_tracker import URL_PATTERN, LinkTracker, _emoji_before_url  # noqa: E402


def test_matches_known_platforms():
    urls = [
        "https://suno.com/song/abc",
        "https://suno.ai/s/xyz",
        "https://youtu.be/abc",
        "https://www.youtube.com/watch?v=abc",
        "https://soundcloud.com/user/track",
        "https://elevenlabs.io/x",
        "https://producer.ai/x",
        "https://cdn1.suno.ai/abc.mp3",
    ]
    for url in urls:
        assert URL_PATTERN.search(url), f"should match: {url}"


def test_rejects_non_music_urls():
    assert URL_PATTERN.search("https://example.com/song") is None
    assert URL_PATTERN.search("https://spotify.com/track/x") is None


def test_get_platform():
    lt = LinkTracker.__new__(LinkTracker)  # no bot needed for this pure method
    assert lt._get_platform("https://suno.com/x") == "Suno"
    assert lt._get_platform("https://suno.ai/x") == "Suno"
    assert lt._get_platform("https://youtu.be/x") == "YouTube"
    assert lt._get_platform("https://www.youtube.com/watch?v=x") == "YouTube"
    assert lt._get_platform("https://soundcloud.com/x") == "SoundCloud"
    assert lt._get_platform("https://elevenlabs.io/x") == "ElevenLabs"
    assert lt._get_platform("https://producer.ai/x") == "Producer.ai"
    assert lt._get_platform("https://example.com/x") == "Unknown"


def test_emoji_prefix_capture():
    assert _emoji_before_url("🔥 https://suno.com/x", "https://suno.com/x") == "🔥"
    assert _emoji_before_url("🎸https://suno.com/x", "https://suno.com/x") == "🎸"
    assert _emoji_before_url("song https://suno.com/x", "https://suno.com/x") is None
    assert _emoji_before_url("https://suno.com/x", "https://suno.com/x") is None


def test_clean_url_strips_trailing_punctuation():
    from cogs.link_tracker import _clean_url
    assert _clean_url("https://soundcloud.com/a/song.") == "https://soundcloud.com/a/song"
    assert _clean_url("https://soundcloud.com/a/song,") == "https://soundcloud.com/a/song"
    assert _clean_url("https://soundcloud.com/a/song)") == "https://soundcloud.com/a/song"
    assert _clean_url("https://soundcloud.com/a/song**") == "https://soundcloud.com/a/song"
    assert _clean_url("https://soundcloud.com/a/song!!") == "https://soundcloud.com/a/song"
    # legit query string must survive
    assert _clean_url("https://soundcloud.com/a/song?si=x&utm_source=y") == \
        "https://soundcloud.com/a/song?si=x&utm_source=y"
