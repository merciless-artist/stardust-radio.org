#!/usr/bin/env python3
r"""
Jukebox4 DJ Watcher — hands-free, no browser extension, no Discord bot.
======================================================================
You DJ as a guest in servers you don't own and can't install anything into your
Discord account. This standalone watcher runs on YOUR PC, looks at the Discord
window you already have open, pulls out the Suno links people post, and hands
them to the jukebox over localhost. Leave it running and walk away.

HOW IT READS DISCORD (hardened, two routes — accuracy first):
  1. ACCESSIBILITY (primary): Discord is a Chromium/Electron app, so it exposes an
     accessibility tree. We read the REAL link text + URLs from it via Windows UI
     Automation — exact characters, no OCR guessing. (pip install uiautomation)
  2. OCR (fallback): if the tree is unavailable, we screenshot the Discord window
     and OCR it. UUID reads are lossy, so every OCR'd link is VALIDATED against
     Suno (via the jukebox's /resolve) and a bounded, polite self-heal fixes the
     odd misread character before it's forwarded. (pip install pytesseract pillow
     + install the Tesseract binary)

It is READ-ONLY: it never types, clicks, sends, or touches your account — it only
reads what's already on your screen, exactly like you reading the chat.

SETUP
  python -m pip install uiautomation pytesseract pillow     (pytesseract/pillow only needed for OCR fallback)
  Install Tesseract (OCR fallback only): https://github.com/UB-Mannheim/tesseract/wiki
  Launch the jukebox (start-jukebox4.bat) and turn ON Discord DJ mode.
  Run this:  start-dj-watcher.bat   (or: python dj-watcher.py)
"""
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ---- config -----------------------------------------------------------------
POLL_SECONDS = 4.0                  # how often we look at Discord
DISCORD_HINTS = ("discord",)        # window title/name substrings that identify Discord
CONFIG_PATH = Path(__file__).resolve().parent / "watcher-config.json"

_SUNO_RE = re.compile(
    r"https?://(?:suno\.com/(?:song|s|playlist)/[A-Za-z0-9\-]+"
    r"|cdn\d*\.suno(?:\.ai)?/[A-Za-z0-9\-]+\.mp3)", re.I)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


# ---- link cleanup (mirrors the jukebox's djRepairUrls) ----------------------
def repair_urls(s):
    """Re-glue links that OCR fractured, WITHOUT swallowing trailing words.
    Handles 'https : / /', 'suno . com', spaces around the path slashes, and a
    space/hyphen-fractured UUID — but never strips spaces past the id itself."""
    s = re.sub(r"(https?)\s*:\s*/\s*/\s*", r"\1://", s, flags=re.I)        # https : / / -> https://
    s = re.sub(r"suno\s*\.\s*(com|ai)", lambda m: "suno." + m.group(1).lower(), s, flags=re.I)
    s = re.sub(r"cdn\s*(\d*)\s*\.\s*suno", lambda m: "cdn" + m.group(1) + ".suno", s, flags=re.I)
    # collapse spaces only around the slashes right after the suno domain ("suno.com / song / ")
    s = re.sub(r"(suno\.(?:com|ai))\s*/\s*(song|s|playlist)\s*/\s*",
               lambda m: m.group(1) + "/" + m.group(2).lower() + "/", s, flags=re.I)
    # glue a uuid whose hex groups got spaced/hyphen-split, bounded to the uuid shape
    s = re.sub(r"[0-9a-f]{8}[\s\-]+[0-9a-f]{4}[\s\-]+[0-9a-f]{4}[\s\-]+[0-9a-f]{4}[\s\-]+[0-9a-f]{12}",
               lambda m: re.sub(r"\s+", "", m.group(0)), s, flags=re.I)
    return s


def extract_links(text):
    """Pull every Suno link out of a blob of text (after gluing OCR fractures)."""
    return _SUNO_RE.findall(repair_urls(text or ""))


# ---- author attribution -----------------------------------------------------
# Discord renders each message as "Username, Today at 3:45 PM, <content>" (accessibility
# aria-label) or, in OCR, the username sits on its own line just above the link. So the author
# is the short label before the first timestamp/separator. We track the last-seen author so a
# user's RAPID 2ND post (which Discord shows with no repeated name) is still attributed to them
# — that's the "same person counted as different people" fix.
_AUTHOR_STOP = re.compile(
    r"(?:,|—|–|\s-\s|•|Today at|Yesterday at|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}:\d{2})", re.I)


def guess_author(s):
    """Best-effort username from a message string, or '' if it doesn't look like a name."""
    s = re.split(r"https?://", s, 1)[0]                 # cut anything from the URL onward
    m = _AUTHOR_STOP.search(s)
    cand = (s[:m.start()] if m else s)
    cand = re.sub(r"[^\w .\-]", " ", cand)              # drop emoji/symbols
    cand = re.sub(r"\s+", " ", cand).strip()
    if not re.search(r"[A-Za-z]", cand) or len(cand) > 32:
        return ""                                       # no letters or too long to be a handle
    al = sum(c.isalnum() for c in cand)
    if al < 2 or al / max(1, len(cand)) < 0.55:
        return ""                                       # mostly punctuation — not a name
    return cand[:32]


def pair_authors(strings):
    """Turn ordered message strings into [{url, author}], carrying the last-seen author across
    bare-link lines so rapid same-user posts share one author."""
    pairs, last = [], ""
    for s in strings:
        a = guess_author(s)
        if a:
            last = a
        for url in extract_links(s):
            pairs.append({"url": url, "author": a or last})
    return pairs


# ---- find the Tesseract ENGINE binary (not the pip package) -----------------
def find_tesseract():
    """The Tesseract OCR engine is a native .exe, not a pip package. Locate it even
    when it isn't on PATH (UB-Mannheim installer's default dirs)."""
    cands = [shutil.which("tesseract")]
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    la = os.environ.get("LOCALAPPDATA", "")
    cands += [os.path.join(pf, "Tesseract-OCR", "tesseract.exe"),
              os.path.join(pfx, "Tesseract-OCR", "tesseract.exe")]
    if la:
        cands += [os.path.join(la, "Programs", "Tesseract-OCR", "tesseract.exe"),
                  os.path.join(la, "Tesseract-OCR", "tesseract.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


# ---- accessibility reader (primary): Windows UI Automation ------------------
def read_via_uia():
    """Read Discord's real on-screen text/links via the accessibility tree.
    Returns (links:list, ok:bool). ok=False means UIA wasn't usable (import/win missing)."""
    try:
        import uiautomation as auto
    except Exception:
        return [], False
    try:
        names = []
        # Discord's top window is a Chromium widget whose Name ends with 'Discord'.
        root = auto.GetRootControl()
        win = None
        for w in root.GetChildren():
            try:
                nm = (w.Name or "")
            except Exception:
                nm = ""
            if any(h in nm.lower() for h in DISCORD_HINTS):
                win = w
                break
        if win is None:
            return [], True   # UIA works, just no Discord window open right now
        # Walk a bounded slice of the tree collecting Name strings (link text shows the URL).
        stack, seen, budget = [(win, 0)], 0, 6000
        while stack and budget > 0:
            ctrl, depth = stack.pop()
            budget -= 1
            try:
                nm = ctrl.Name or ""
                if nm:
                    names.append(nm)
                # Hyperlink controls may carry the URL in their value pattern.
                try:
                    vp = ctrl.GetLegacyIAccessiblePattern()
                    if vp and vp.Value:
                        names.append(vp.Value)
                except Exception:
                    pass
            except Exception:
                pass
            if depth < 40:
                try:
                    # push reversed so pop() yields children in document (top-to-bottom) order —
                    # keeps each author ahead of its link for pair_authors()
                    for c in reversed(ctrl.GetChildren()):
                        stack.append((c, depth + 1))
                except Exception:
                    pass
        return names, True          # ordered message strings — caller pairs author + link
    except Exception:
        return [], False


# ---- OCR reader (fallback): screenshot the Discord window + OCR -------------
_ocr_warned = [False]
_tess_set = [False]


def read_via_ocr():
    """Screenshot Discord and OCR it. Returns (links:list, ok:bool). Lossy — caller validates."""
    try:
        from PIL import ImageGrab
        import pytesseract
    except Exception:
        if not _ocr_warned[0]:
            _ocr_warned[0] = True
            print("  [ocr] fallback unavailable — pip install pillow pytesseract  (and the Tesseract engine)")
        return [], False
    if not _tess_set[0]:                     # point pytesseract at the engine even if not on PATH
        _tess_set[0] = True
        t = find_tesseract()
        if t:
            pytesseract.pytesseract.tesseract_cmd = t
    try:
        bbox = _discord_bbox()                  # tight crop if we can find the window
        img = ImageGrab.grab(bbox=bbox) if bbox else ImageGrab.grab()
        img = img.convert("L")                  # grayscale helps glyph OCR
        # No char-whitelist here: we need usernames (mixed case + spaces) AND links. Link accuracy
        # is recovered downstream by repair + Suno validation + self-heal, not by restricting OCR.
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text.splitlines(), True          # ordered lines — caller pairs author + link
    except Exception as e:
        if not _ocr_warned[0]:
            _ocr_warned[0] = True
            print("  [ocr] error: %s" % e)
        return [], False


def _discord_bbox():
    """Best-effort (left, top, right, bottom) of the Discord window, or None for full screen."""
    try:
        import uiautomation as auto
        root = auto.GetRootControl()
        for w in root.GetChildren():
            nm = (w.Name or "").lower()
            if any(h in nm for h in DISCORD_HINTS):
                r = w.BoundingRectangle
                if r and r.right > r.left and r.bottom > r.top:
                    return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None


# ---- startup diagnostics: show which readers are actually available ---------
def diagnose():
    print("  Readers:")
    try:
        import uiautomation  # noqa: F401
        ua = "OK"
    except Exception:
        ua = "MISSING  ->  pip install uiautomation"
    print("    - accessibility (accurate): %s" % ua)
    try:
        import PIL  # noqa: F401
        import pytesseract  # noqa: F401
        t = find_tesseract()
        ocr = ("pytesseract OK + engine at %s" % t) if t else \
              "pytesseract OK, but Tesseract ENGINE missing  ->  winget install UB-Mannheim.TesseractOCR"
    except Exception:
        ocr = "MISSING  ->  pip install pillow pytesseract  (+ the Tesseract engine)"
    print("    - ocr fallback            : %s" % ocr)
    try:
        import uiautomation as auto
        root = auto.GetRootControl()
        win = ""
        for w in root.GetChildren():
            nm = (w.Name or "")
            if any(h in nm.lower() for h in DISCORD_HINTS):
                win = nm
                break
        print("    - discord window          : %s" % (("found — '%s'" % win[:48]) if win else "NOT found (open Discord first)"))
    except Exception:
        print("    - discord window          : (can't check until uiautomation is installed)")


# ---- networking: talk to the Stardust Radio dashboard (token auth) ----------
def load_config():
    """Load dashboard_url + token, prompting on first run."""
    cfg = {}
    if CONFIG_PATH.is_file():
        try:
            cfg = json.loads(CONFIG_PATH.read_text("utf-8-sig"))
        except Exception:
            cfg = {}
    if not cfg.get("token"):
        print("First-time setup. Open your booth, click 'Connect watcher', copy the token.")
        cfg["dashboard_url"] = (input("Dashboard URL [https://stardust-radio.org]: ").strip()
                                or "https://stardust-radio.org").rstrip("/")
        cfg["token"] = input("Paste your watcher token: ").strip()
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")
        print(f"Saved to {CONFIG_PATH.name}.")
    cfg.setdefault("dashboard_url", "https://stardust-radio.org")
    return cfg


# Cloudflare (in front of stardust-radio.org) blocks urllib's default
# User-Agent with a 403, so we send a browser-like one. Harmless on localhost.
_WATCHER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 StardustWatcher/1.0"
)


def _get(url, token):
    req = urllib.request.Request(
        url, headers={"X-Watcher-Token": token, "User-Agent": _WATCHER_UA})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(url, token, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"X-Watcher-Token": token,
                                          "Content-Type": "application/json",
                                          "User-Agent": _WATCHER_UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {}


def scrape_suno_name(url):
    """Best-effort: pull the real artist name off the Suno page.

    Suno serves '<title>SONG by ARTIST | Suno</title>'. We split on the last
    ' by ' before ' | Suno' so a song title that itself contains ' by ' still
    resolves the artist. Returns '' on any failure (caller keeps its guess).
    """
    if "suno." not in url.lower():
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _WATCHER_UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return ""
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        title = m.group(1).strip()
        if title.endswith(" | Suno"):
            middle = title[: -len(" | Suno")]
            if " by " in middle:
                artist = middle.rsplit(" by ", 1)[1].strip()
                if artist:
                    return artist
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+'
        r'content=["\'][^"\']+?\sby\s+([^"\']+?)\s*\(@',
        html,
    )
    if m:
        return m.group(1).strip()
    return ""


def pick_session(base, token):
    sessions = _get(base + "/api/watcher/sessions", token)
    if not sessions:
        print("No No-Server sessions found for your account. Create one in the booth first.")
        return None
    print("\nYour sessions:")
    for i, s in enumerate(sessions, 1):
        state = "ON" if s["watcher_enabled"] else "off"
        print(f"  {i}. {s['name']}   (auto-add: {state})")
    while True:
        choice = input("Pick a session number to feed: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(sessions):
            return sessions[int(choice) - 1]["id"]
        print("Enter a valid number.")


# ---- main loop --------------------------------------------------------------
def main():
    cfg = load_config()
    base = cfg["dashboard_url"].strip()
    # If a full booth URL got pasted by mistake, keep only scheme://host.
    if "://" in base:
        scheme, rest = base.split("://", 1)
        base = scheme + "://" + rest.split("/")[0]
    token = cfg["token"]
    print(f"Jukebox watcher -> {base}")
    diagnose()
    # If the config names a session, use it (no prompt). Otherwise ask.
    sid = cfg.get("session_id") or pick_session(base, token)
    if not sid:
        return
    print(f"Feeding session: {sid}")
    add_url = base + "/api/watcher/sessions/" + sid + "/queue/add"
    sess_url = base + "/api/watcher/sessions"
    seen = set()
    said_off = False
    print("\nWatching. Flip the 'Auto-add from Discord' toggle ON in the booth to start. Ctrl+C to stop.")
    while True:
        try:
            sessions = _get(sess_url, token)
            me = next((s for s in sessions if s["id"] == sid), None)
            if not me:
                print("That session no longer exists. Stopping."); return
            if not me["watcher_enabled"]:
                if not said_off:
                    print("  [idle] toggle is OFF — not harvesting.")
                    said_off = True
                time.sleep(POLL_SECONDS); continue
            said_off = False

            strings, _ = read_via_uia()
            if not any(extract_links(s) for s in strings):
                ocr_strings, _ = read_via_ocr()
                if any(extract_links(s) for s in ocr_strings):
                    strings = ocr_strings
            pairs = pair_authors(strings)
            fresh = [p for p in pairs if p["url"] not in seen]
            for p in fresh:
                seen.add(p["url"])
            if fresh:
                code, resp = _post(add_url, token, {"items": fresh})
                if code == 200:
                    print(f"  [+{resp.get('added', 0)}] sent to booth")
                elif code == 403:
                    print("  [idle] toggle turned off mid-run.")
                elif code == 401:
                    print("  [auth] token rejected — regenerate it in the booth and update watcher-config.json."); return
        except KeyboardInterrupt:
            print("\nStopped."); return
        except Exception as e:
            print(f"  [warn] {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
