# Installs a Windows Task Scheduler entry that runs scripts\daily.bat.
# Missed runs (PC off at trigger time) are launched once after the machine is
# available again thanks to StartWhenAvailable.
# Runs as the current user, interactive only, least privilege.
# No password/secret is stored anywhere.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -At "09:30"
#   powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -DayOfWeek Monday,Thursday -At "20:00"
#
# WHEN it runs comes from config.toml [schedule] cron - that file is the single
# source of truth, so there is no second place to keep in sync. The parameters
# above only override it for one-off re-registrations; leave them off normally.
#
# The trigger uses WINDOWS LOCAL TIME. The machine should be on China Standard
# Time (Asia/Shanghai) so 08:00 means the same wall clock the config.toml uses.
#
# This script only reads environment variables; it never writes your API key
# into the task, the scripts, or the repo.
param(
    [string]$TaskName  = 'DailyBrief',
    # 留空即用 config.toml 的 cron。下面两个只在临时覆盖时给。
    [switch]$Weekly,
    [ValidateSet('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')]
    [string[]]$DayOfWeek = @(),
    [string]$At        = '',
    [string]$RepoRoot  = ''
)

$ErrorActionPreference = 'Stop'

# 这个脚本的 stdout 会被 apply_config.py 捕获再打印。PS 5.1 默认按控制台代码页
# 输出，中文路径到了 Python 那头就是乱码 —— 这里直接把输出编码定成 UTF-8。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
$BatchFile = Join-Path $PSScriptRoot 'daily.bat'

# --- WHEN to run: config.toml [schedule] 是唯一来源 --------------------------
# 以前 -At 默认写死 '08:00'，和 config.toml 里的 cron 各说各话 —— 改时间要改两处，
# 漏一处就静默不一致。现在默认去读 config.toml，命令行参数只在临时覆盖时才给。
function Get-ConfigValue {
    param([string]$ConfigPath, [string]$Section, [string]$Key)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return $null }
    $inSection = $false
    foreach ($line in (Get-Content -LiteralPath $ConfigPath -Encoding UTF8)) {
        $t = $line.Trim()
        if ($t -match '^\[(.+?)\]$') {
            $inSection = ($Matches[1].Trim() -eq $Section)
            continue
        }
        if (-not $inSection -or $t -eq '' -or $t.StartsWith('#')) { continue }
        if ($t -match ('^' + [regex]::Escape($Key) + '\s*=\s*"([^"]*)"')) {
            return $Matches[1]
        }
    }
    return $null
}

# 只认定点 cron（分、时是具体数字）——和 apply_config.py 的 parse_cron 同一套限制。
function ConvertFrom-Cron {
    param([string]$Cron)
    $p = $Cron.Trim() -split '\s+'
    if ($p.Count -ne 5) { throw ('cron 要 5 段「分 时 日 月 星期」，拿到：' + $Cron) }
    if ($p[0] -notmatch '^\d+$' -or $p[1] -notmatch '^\d+$') {
        throw ('只支持定点 cron（分和时是具体数字），拿到：' + $Cron)
    }
    $at = ('{0:00}:{1:00}' -f [int]$p[1], [int]$p[0])
    if ($p[4] -eq '*') { return @{ At = $at; Days = @() } }
    $map = @{ '0'='Sunday'; '1'='Monday'; '2'='Tuesday'; '3'='Wednesday'
              '4'='Thursday'; '5'='Friday'; '6'='Saturday'; '7'='Sunday' }
    $days = @()
    foreach ($d in ($p[4] -split ',')) {
        $k = $d.Trim()
        if (-not $map.ContainsKey($k)) { throw ('cron 的星期字段看不懂：' + $p[4]) }
        $days += $map[$k]
    }
    return @{ At = $at; Days = $days }
}

$time = ''
$days = @()
$source = 'default'
try {
    $cron = Get-ConfigValue -ConfigPath (Join-Path $RepoRoot 'config.toml') `
                            -Section 'schedule' -Key 'cron'
    if ($cron) {
        $parsed = ConvertFrom-Cron -Cron $cron
        $time = $parsed.At
        $days = $parsed.Days
        $source = 'config.toml [schedule] cron = "' + $cron + '"'
    }
} catch {
    Write-Warning ($_.Exception.Message + ' —— 回落到每天 08:00')
}

# 命令行显式给了就覆盖 config.toml
if ($At) { $time = $At; $source = '-At（命令行覆盖）' }
if ($Weekly) {
    $days = $(if ($DayOfWeek.Count) { $DayOfWeek } else { @('Monday') })
    $source = '-Weekly（命令行覆盖）'
} elseif ($DayOfWeek.Count) {
    $days = $DayOfWeek
    $source = '-DayOfWeek（命令行覆盖）'
}
if (-not $time) { $time = '08:00' }
if ($time -notmatch '^\d{1,2}:\d{2}$') {
    throw ('-At 格式应为 HH:MM，拿到：' + $time)
}

Write-Host ('Repo     : ' + $RepoRoot)
Write-Host ('Task     : ' + $TaskName)
$cadence = if ($days.Count) { 'every ' + ($days -join ', ') } else { 'every day' }
Write-Host ('Schedule : ' + $cadence + ' at ' + $time + '  (Windows local time)')
Write-Host ('Source   : ' + $source)
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
    Write-Warning '  the clock time comes from config.toml [schedule] cron, so adjust it there if 08:00 Asia/Shanghai is not what you want.'
}
Write-Host ''

# --- build the task ----------------------------------------------------------
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/d /c "' + $BatchFile + '"') `
    -WorkingDirectory $RepoRoot

if ($days.Count) {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $time
    $desc = 'DailyBrief: run scripts\daily.bat on ' + ($days -join ', ') + ' at ' + $time +
            '; catch up after boot when the PC was off.'
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
    $desc = 'DailyBrief: run scripts\daily.bat daily at ' + $time +
            '; catch up after boot when the PC was off.'
}

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
    -Description $desc `
    -Force | Out-Null

Write-Host 'Registered/updated task.'
Write-Host ''
Write-Host 'Verify / run / remove:'
Write-Host ('  schtasks /query /tn ' + $TaskName + ' /v')
Write-Host ('  schtasks /run  /tn ' + $TaskName)
Write-Host ('  powershell -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'uninstall_task.ps1') + '"')
