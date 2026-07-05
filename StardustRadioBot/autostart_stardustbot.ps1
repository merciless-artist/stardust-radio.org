# ◸──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◹
#       SECTION: Stardust Radio Bot Autostart (boot-safe, no duplicates)
# ◺──────── ✧ ────────🔹-💠-🔹 ──────── ◇ ———————◿
#
# Starts the Stardust Radio Bot ONLY if it isn't already running (guards on the
# unique marker arg so you never get two copies fighting over the Discord
# token), and waits for the local MariaDB before launching. Hidden + detached.

$ErrorActionPreference = 'Stop'

$botDir = 'C:\Users\Merci\Downloads\MUSIC_WRITERS_BOT_SYSTEM\MUSIC_WRITERS_BOT_SYSTEM\A studio\4 Stardust Bot'
$python = 'C:\Program Files\Python313\python.exe'
$marker = 'STARDUST_RADIO_BOT'   # unique tag in the command line = "already running?" check

# ── Guard: already alive? ────────────────────────────────────────────────────
$already = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match $marker }
if ($already) { return }

# ── Wait (up to ~3 min) for the local MariaDB (Docker) to accept connections ──
for ($i = 0; $i -lt 90; $i++) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect('127.0.0.1', 3306)
        $tcp.Close()
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

# ── Launch hidden + detached, with logs ──────────────────────────────────────
$out = Join-Path $botDir 'bot.out.log'
$err = Join-Path $botDir 'bot.err.log'
Start-Process -FilePath $python `
    -ArgumentList '-u', 'app.py', $marker `
    -WorkingDirectory $botDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err
