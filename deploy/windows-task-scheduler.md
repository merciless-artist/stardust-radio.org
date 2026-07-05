# Auto-starting the Dashboard on Windows

The dashboard is a Flask app on `127.0.0.1:8080`. On Windows you generally want it to launch at login and self-restart if it crashes. Task Scheduler handles this well.

## Create the task

Open **Task Scheduler** and create a task with:

- **General**
  - Name: `StardustRadio_Listener`
  - Run whether user is logged on or not: no (this task is per-user)
  - Configure for: your Windows version

- **Triggers**
  - New → At log on → for your user

- **Actions**
  - New → Start a program
  - Program/script: `C:\Path\To\Python\pythonw.exe` (use `pythonw.exe`, not `python.exe`, so there's no console window)
  - Arguments: `server.py`
  - Start in: `C:\Path\To\Repo\Dashboard`

- **Conditions**
  - Uncheck "Start the task only if the computer is on AC power"

- **Settings**
  - Allow task to be run on demand: yes
  - If the task fails, restart every: **1 minute**
  - Attempt to restart up to: **3 times**
  - Stop the task if it runs longer than: **(disabled — this is a long-running server)**
  - If the running task does not end when requested, force it to stop: yes

## Verify

After creating the task, log out and back in. Then in PowerShell:

```powershell
netstat -ano | findstr ":8080"
```

You should see something listening on `127.0.0.1:8080`.

## PowerShell one-liner (admin)

If you'd rather script it — run this in an elevated PowerShell (adjust the two paths):

```powershell
$action = New-ScheduledTaskAction `
    -Execute "C:\Path\To\Python\pythonw.exe" `
    -Argument "server.py" `
    -WorkingDirectory "C:\Path\To\Repo\Dashboard"

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "StardustRadio_Listener" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Limited

Start-ScheduledTask -TaskName "StardustRadio_Listener"
```

## Troubleshooting

- **Nothing on port 8080 after login** — check Task Scheduler > Task History for the error. Common causes: wrong Python path, missing `pythonw.exe` (only `python.exe` installed), or the venv isn't activated (in which case point at the venv's `pythonw.exe`).
- **Task shows "The operator or administrator has refused the request"** — you unchecked "Allow task to be run on demand" or the account can't log on interactively.
- **Server crashes and doesn't restart** — check the restart-on-failure settings. `RestartCount` must be > 0 and `RestartInterval` set.
