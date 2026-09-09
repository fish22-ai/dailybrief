# Installs a Windows Task Scheduler entry that runs scripts\daily.bat weekly.
# Missed runs (PC off at trigger time) are launched once after the machine is
# available again thanks to StartWhenAvailable. Runs as the current user,
# interactive only, least privilege. No password/secret is stored anywhere.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -DayOfWeek Sunday -At "20:00"
#
# The trigger uses WINDOWS LOCAL TIME. The machine should be on China Standard
# Time (Asia/Shanghai) so 08:00 means the same wall clock the config.toml uses.
#
# This script only reads environment variables; it never writes your API key
# into the task, the scripts, or the repo.
param(
    [string]$TaskName  = 'DailyBrief',
    [ValidateSet('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')]
    [string]$DayOfWeek = 'Monday',
    [string]$At        = '08:00',
    [string]$RepoRoot  = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
$BatchFile = Join-Path $PSScriptRoot 'daily.bat'

Write-Host ('Repo     : ' + $RepoRoot)
Write-Host ('Task     : ' + $TaskName)
Write-Host ('Schedule : every ' + $DayOfWeek + ' at ' + $At + '  (Windows local time)')
Write-Host ''

# --- sanity checks ----------------------------------------------------------
if (-not (Test-Path -LiteralPath $BatchFile)) {
    throw ('daily.bat not found: ' + $BatchFile)
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Warning 'python not found on PATH - scheduled runs will fail.'
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Warning 'git not found on PATH - scheduled runs will fail.'
}

$key  = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
$base = [Environment]::GetEnvironmentVariable('ANTHROPIC_BASE_URL', 'User')
if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Warning 'User env ANTHROPIC_API_KEY is NOT set yet - runs would fall back to rules mode (no AI notes).'
    Write-Host '  Set it once in cmd, then REOPEN the terminal so new processes see it:'
    Write-Host '    setx ANTHROPIC_API_KEY "<your key>"'
} else {
    Write-Host ('ANTHROPIC_API_KEY : set (length ' + $key.Length + ')')
}
if ([string]::IsNullOrWhiteSpace($base)) {
    Write-Warning 'User env ANTHROPIC_BASE_URL is NOT set. Third-party keys need it, e.g. https://agentrouter.org'
    Write-Host '    setx ANTHROPIC_BASE_URL "https://agentrouter.org"'
} else {
    Write-Host ('ANTHROPIC_BASE_URL: ' + $base)
}

$tz = (Get-TimeZone).Id
if ($tz -notmatch 'China') {
    Write-Warning ('Machine timezone is ' + $tz + ' (not China Standard Time). The trigger uses Windows local time;')
    Write-Warning '  adjust -DayOfWeek/-At if 08:00 Asia/Shanghai is not what you want on this machine.'
}
Write-Host ''

# --- build the task ----------------------------------------------------------
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/d /c "' + $BatchFile + '"') `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $At

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'DailyBrief: run scripts\daily.bat weekly; catch up after boot when the PC was off.' `
    -Force | Out-Null

Write-Host 'Registered/updated task.'
Write-Host ''
Write-Host 'Verify / run / remove:'
Write-Host ('  schtasks /query /tn ' + $TaskName + ' /v')
Write-Host ('  schtasks /run  /tn ' + $TaskName)
Write-Host ('  powershell -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'uninstall_task.ps1') + '"')
