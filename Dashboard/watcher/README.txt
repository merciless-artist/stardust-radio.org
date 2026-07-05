No-Server Mode — Discord Watcher (host quick-start)

What it does: reads Suno links people post in your Discord window and adds
them to your No-Server session automatically. Runs on YOUR pc. Read-only —
it never types or clicks in Discord.

Setup (once):
  1. In the booth, open your No-Server session, click "Connect watcher",
     and copy the token it shows.
  2. Double-click start-watcher.bat. Paste the token when asked
     (and press Enter to accept the default dashboard URL).
  3. Pick which session to feed from the list.

Daily use:
  - Have your Discord window open and visible.
  - Flip "Auto-add from Discord" ON in that session when you want it to grab
    links; flip it OFF to stop. The watcher obeys the toggle.

Notes:
  - Needs Python 3.10+ installed. The launcher installs 'uiautomation'.
  - The token only lets the watcher add to YOUR sessions. Keep it private;
    you can "regenerate" it in the booth to invalidate the old one.
