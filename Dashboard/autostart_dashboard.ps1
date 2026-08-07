# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Stardust Dashboard Autostart Launcher (boot-safe)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
#
# Run by the Startup .vbs at logon (and can be run by hand). Starts the Flask
# dashboard ONLY if it isn't already running, so you can never end up with two
# copies fighting over port 8080. Launches hidden + detached with logging.

$ErrorActionPreference = 'Stop'

$dashDir = 'C:\Users\Merci\Projects\StardustRadio\Dashboard'
$python  = 'C:\Program Files\Python313\python.exe'

# ── Guard: is the dashboard already alive? ───────────────────────────────────
$already = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'server\.py' }
if ($already) { return }

# ── Launch hidden + detached, logging so we can always see its state ──────────
$out = Join-Path $dashDir 'dash.out.log'
$err = Join-Path $dashDir 'dash.err.log'
Start-Process -FilePath $python `
    -ArgumentList 'server.py' `
    -WorkingDirectory $dashDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err
