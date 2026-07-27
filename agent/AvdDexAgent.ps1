<#
.SYNOPSIS
  AVD DEX agent - samples in-session experience telemetry on a session host
  and POSTs it to the AVD DEX Dashboard ingest API.

.DESCRIPTION
  Collects, once per run (schedule it every minute via the installer):

    Per session (mapped session id -> user via quser):
      - Max input delay (ms)   \User Input Delay per Session(*)\Max Input Delay
      - Working set (MB)       sum of process working sets per session

    Host level:
      - CPU %                  \Processor(_Total)\% Processor Time
      - Free memory (MB)       \Memory\Available MBytes
      - Disk latency (ms)      \LogicalDisk(_Total)\Avg. Disk sec/Read|Write
      - RDP RTT / bandwidth    \RemoteFX Network(*)\Current TCP RTT / Bandwidth
      - Encoding time / FPS    \RemoteFX Graphics(*) counters

    Events since the previous run:
      - App crashes / hangs    Application log, Ids 1000 / 1002
      - FSLogix profile loads  Microsoft-FSLogix-Apps/Operational

  Payloads that cannot be delivered are spooled to disk and retried on the
  next run. Every collector is individually try/catch'd: a missing counter
  or log never stops the rest of the sample.

.NOTES
  Run as SYSTEM (the installer registers a scheduled task). ASCII-only on
  purpose so Windows PowerShell 5.1 parses it regardless of encoding.
#>

[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'agent-config.json')
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------- config --
if (-not (Test-Path $ConfigPath)) {
    Write-Error "Config not found: $ConfigPath (run Install-AvdDexAgent.ps1)"
    exit 1
}
$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$serverUrl = $cfg.ServerUrl.TrimEnd('/')
$apiKey    = $cfg.ApiKey
$hostPool  = $cfg.HostPool
$spoolDir  = Join-Path $PSScriptRoot 'spool'
$statePath = Join-Path $PSScriptRoot 'state.json'
if (-not (Test-Path $spoolDir)) { New-Item -ItemType Directory -Path $spoolDir -Force | Out-Null }

# Window for event collection: since last successful run (capped at 30 min).
$now = (Get-Date).ToUniversalTime()
$since = $now.AddMinutes(-2)
if (Test-Path $statePath) {
    try {
        $state = Get-Content $statePath -Raw | ConvertFrom-Json
        $last = [datetime]::Parse($state.LastRunUtc).ToUniversalTime()
        if ($last -gt $now.AddMinutes(-30)) { $since = $last }
    } catch { }
}

function Get-CounterAvg {
    param([string]$Path)
    try {
        $samples = (Get-Counter -Counter $Path -ErrorAction Stop).CounterSamples |
            Where-Object { $_.CookedValue -ne $null }
        if (-not $samples) { return $null }
        $vals = foreach ($s in $samples) { [double]$s.CookedValue }
        return [math]::Round(($vals | Measure-Object -Average).Average, 2)
    } catch { return $null }
}

function ConvertTo-IdleMinutes {
    # quser IDLE TIME formats: "." / "none", "45" (min), "2:30" (h:mm),
    # "1+02:30" (days+h:mm)
    param([string]$Text)
    if (-not $Text -or $Text -eq '.' -or $Text -match '(?i)none') { return 0 }
    $days = 0; $t = $Text
    if ($t -match '^(\d+)\+(.+)$') { $days = [int]$Matches[1]; $t = $Matches[2] }
    if ($t -match '^(\d+):(\d+)$') {
        return $days * 1440 + [int]$Matches[1] * 60 + [int]$Matches[2]
    }
    if ($t -match '^\d+$') { return $days * 1440 + [int]$t }
    return $null
}

function Get-SessionMap {
    # session id -> @{ User; State; IdleMin }, from quser
    $map = @{}
    try {
        $lines = (quser) 2>$null
        foreach ($line in ($lines | Select-Object -Skip 1)) {
            if ($line -match '^\s*>?(?<user>\S+)\s+(?:(?<session>\S+)\s+)?(?<id>\d+)\s+(?<state>\S+)\s+(?<idle>\S+)') {
                $map[[int]$Matches['id']] = @{
                    User    = $Matches['user']
                    State   = $Matches['state']
                    IdleMin = ConvertTo-IdleMinutes $Matches['idle']
                }
            }
        }
    } catch { }
    return $map
}

# --------------------------------------------------------------- collect --
$sessionMap = Get-SessionMap
$sessions = New-Object System.Collections.ArrayList

# Input delay per session: instance names are session ids ("1", "2", ...)
try {
    $idSamples = (Get-Counter '\User Input Delay per Session(*)\Max Input Delay' -ErrorAction Stop).CounterSamples
    foreach ($s in $idSamples) {
        if ($s.InstanceName -notmatch '^\d+$') { continue }
        $sid = [int]$s.InstanceName
        $info = $sessionMap[$sid]
        if (-not $info) { continue }
        [void]$sessions.Add(@{
            session_id     = $sid
            user           = $info.User
            state          = $info.State
            idle_min       = $info.IdleMin
            input_delay_ms = [math]::Round([double]$s.CookedValue, 1)
            mem_mb         = $null
        })
    }
} catch { }

# Working set per session (needs elevated/SYSTEM for -IncludeUserName)
try {
    $bySession = Get-Process | Group-Object SessionId
    foreach ($g in $bySession) {
        $sid = [int]$g.Name
        if ($sid -eq 0 -or -not $sessionMap.ContainsKey($sid)) { continue }
        $memMb = [math]::Round((($g.Group | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 0)
        $existing = $sessions | Where-Object { $_.session_id -eq $sid } | Select-Object -First 1
        if ($existing) { $existing.mem_mb = $memMb }
        else {
            $info = $sessionMap[$sid]
            [void]$sessions.Add(@{
                session_id = $sid; user = $info.User
                state = $info.State; idle_min = $info.IdleMin
                input_delay_ms = $null; mem_mb = $memMb
            })
        }
    }
} catch { }

$diskRead  = Get-CounterAvg '\LogicalDisk(_Total)\Avg. Disk sec/Read'
$diskWrite = Get-CounterAvg '\LogicalDisk(_Total)\Avg. Disk sec/Write'
$rttTcp    = Get-CounterAvg '\RemoteFX Network(*)\Current TCP RTT'
$rttUdp    = Get-CounterAvg '\RemoteFX Network(*)\Current UDP RTT'
$bwTcp     = Get-CounterAvg '\RemoteFX Network(*)\Current TCP Bandwidth'
$bwUdp     = Get-CounterAvg '\RemoteFX Network(*)\Current UDP Bandwidth'
$rtt = if ($rttUdp -gt 0) { $rttUdp } elseif ($rttTcp -gt 0) { $rttTcp } else { $null }
$bw  = if ($bwUdp -gt 0) { $bwUdp } elseif ($bwTcp -gt 0) { $bwTcp } else { $null }
# UDP (Shortpath) in use is itself a DEX signal - TCP-only usually means
# a network/gateway problem.
$udpActive = ($rttUdp -gt 0 -or $bwUdp -gt 0)

# Packet loss: counter name varies by OS build; try known variants.
$loss = Get-CounterAvg '\RemoteFX Network(*)\Loss Rate'
if ($loss -eq $null) { $loss = Get-CounterAvg '\RemoteFX Network(*)\Current Loss Rate' }
if ($loss -eq $null) { $loss = Get-CounterAvg '\RemoteFX Network(*)\Current UDP Packet Loss Rate' }

$fpsIn      = Get-CounterAvg '\RemoteFX Graphics(*)\Input Frames/Second'
$fpsOut     = Get-CounterAvg '\RemoteFX Graphics(*)\Output Frames/Second'
$fps = if ($fpsOut -gt 0) { $fpsOut } elseif ($fpsIn -gt 0) { $fpsIn } else { $null }
$encoding   = Get-CounterAvg '\RemoteFX Graphics(*)\Average Encoding Time'
$skipServer = Get-CounterAvg '\RemoteFX Graphics(*)\Frames Skipped/Second - Insufficient Server Resources'
$skipNet    = Get-CounterAvg '\RemoteFX Graphics(*)\Frames Skipped/Second - Insufficient Network Resources'
$skipClient = Get-CounterAvg '\RemoteFX Graphics(*)\Frames Skipped/Second - Insufficient Client Resources'
$skipped = $null
$skipVals = @($skipServer, $skipNet, $skipClient) | Where-Object { $_ -ne $null }
if ($skipVals.Count -gt 0) { $skipped = [math]::Round(($skipVals | Measure-Object -Sum).Sum, 2) }

$hostMetrics = @{
    cpu_pct           = Get-CounterAvg '\Processor(_Total)\% Processor Time'
    mem_free_mb       = Get-CounterAvg '\Memory\Available MBytes'
    disk_read_ms      = if ($diskRead -ne $null)  { [math]::Round($diskRead * 1000, 1) }  else { $null }
    disk_write_ms     = if ($diskWrite -ne $null) { [math]::Round($diskWrite * 1000, 1) } else { $null }
    rtt_ms            = $rtt
    bandwidth_kbps    = if ($bw -ne $null) { [math]::Round($bw / 8000, 0) } else { $null }  # bps -> KBps
    encoding_ms       = $encoding
    fps               = $fps
    frames_skipped_ps = $skipped
    packet_loss_pct   = $loss
    udp_active        = $udpActive
}

# ---------------------------------------------------------------- events --
$events = New-Object System.Collections.ArrayList

try {
    $crashes = Get-WinEvent -FilterHashtable @{
        LogName = 'Application'; Id = 1000, 1002; StartTime = $since.ToLocalTime()
    } -ErrorAction Stop
    foreach ($ev in $crashes) {
        $app = $null
        try { $app = [string]$ev.Properties[0].Value } catch { }
        [void]$events.Add(@{
            kind    = if ($ev.Id -eq 1002) { 'app_hang' } else { 'app_crash' }
            source  = $app
            ts      = $ev.TimeCreated.ToUniversalTime().ToString('o')
            message = ($ev.Message -split "`n")[0]
        })
    }
} catch { }

try {
    $fsx = Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-FSLogix-Apps/Operational'; StartTime = $since.ToLocalTime()
    } -ErrorAction Stop
    foreach ($ev in $fsx) {
        if ($ev.Message -match '(?i)load.*profile|profile.*load' -and
            $ev.Message -match '(\d+)\s*(milliseconds|ms)') {
            $user = $null
            if ($ev.Message -match '(?i)user[:\s]+(\S+)') { $user = $Matches[1] }
            [void]$events.Add(@{
                kind        = 'profile_load'
                user        = $user
                duration_ms = [double]$Matches[1]
                ts          = $ev.TimeCreated.ToUniversalTime().ToString('o')
                message     = ($ev.Message -split "`n")[0]
            })
        }
    }
} catch { }

# ------------------------------------------------------------------ send --
$payload = @{
    host          = $env:COMPUTERNAME
    hostpool      = $hostPool
    agent_version = '1.0'
    timestamp     = $now.ToString('o')
    host_metrics  = $hostMetrics
    sessions      = @($sessions)
    events        = @($events)
}

function Send-Payload {
    param([string]$Json)
    Invoke-RestMethod -Method Post -Uri "$serverUrl/api/agent/ingest" `
        -Headers @{ 'X-Agent-Key' = $apiKey } `
        -Body $Json -ContentType 'application/json' -TimeoutSec 15 | Out-Null
}

$json = $payload | ConvertTo-Json -Depth 6
$sent = $false
try {
    Send-Payload -Json $json
    $sent = $true
} catch {
    # Spool for retry; cap the spool at 500 files (drop oldest).
    $name = 'sample-{0}.json' -f $now.ToString('yyyyMMdd-HHmmss')
    [System.IO.File]::WriteAllText((Join-Path $spoolDir $name), $json, [System.Text.Encoding]::UTF8)
    $spooled = Get-ChildItem $spoolDir -Filter 'sample-*.json' | Sort-Object Name
    if ($spooled.Count -gt 500) {
        $spooled | Select-Object -First ($spooled.Count - 500) |
            Remove-Item -Force -Confirm:$false
    }
}

# Retry up to 20 spooled payloads per run.
if ($sent) {
    $retry = Get-ChildItem $spoolDir -Filter 'sample-*.json' |
        Sort-Object Name | Select-Object -First 20
    foreach ($f in $retry) {
        try {
            Send-Payload -Json (Get-Content $f.FullName -Raw)
            Remove-Item $f.FullName -Force -Confirm:$false
        } catch { break }
    }
}

# ----------------------------------------------------------------- state --
@{ LastRunUtc = $now.ToString('o') } | ConvertTo-Json |
    Set-Content -Path $statePath -Encoding Ascii
