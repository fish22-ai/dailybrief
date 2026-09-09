# Removes only the DailyBrief scheduled task (default name). Leaves repo files,
# data/, site/, logs/ and environment variables untouched.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1 -TaskName SomethingElse
param(
    [string]$TaskName = 'DailyBrief'
)

$ErrorActionPreference = 'Stop'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host ("Task '" + $TaskName + "' is not installed - nothing to remove.")
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host ("Removed scheduled task '" + $TaskName + "'.")
Write-Host 'Repo files, data, site, logs and env vars were left untouched.'
