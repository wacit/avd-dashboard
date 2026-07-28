<#
.SYNOPSIS
  Installs (or removes) the AVD DEX agent on a session host.

.DESCRIPTION
  Copies AvdDexAgent.ps1 to "C:\Program Files\AvdDexAgent", writes the
  agent config (server URL, API key, host pool), and registers a scheduled
  task that runs the agent every minute as SYSTEM.

.EXAMPLE
  .\Install-AvdDexAgent.ps1 -ServerUrl http://dashboard-host:8000 -ApiKey <key> -HostPool Contoso-Pool1

.EXAMPLE
  .\Install-AvdDexAgent.ps1 -Uninstall
#>

[CmdletBinding(DefaultParameterSetName = 'Install')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Install')]
    [string]$ServerUrl,

    [Parameter(Mandatory, ParameterSetName = 'Install')]
    [string]$ApiKey,

    [Parameter(ParameterSetName = 'Install')]
    [string]$HostPool = '',

    [Parameter(ParameterSetName = 'Install')]
    [int]$IntervalMinutes = 1,

    [Parameter(Mandatory, ParameterSetName = 'Uninstall')]
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$taskName   = 'AvdDexAgent'
$installDir = 'C:\Program Files\AvdDexAgent'

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session.'
}

if ($Uninstall) {
    try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false } catch { }
    if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force -Confirm:$false }
    Write-Host "AVD DEX agent removed."
    exit 0
}

# ----------------------------------------------------------------- files --
$src = Join-Path $PSScriptRoot 'AvdDexAgent.ps1'
if (-not (Test-Path $src)) { throw "AvdDexAgent.ps1 not found next to this installer." }

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
Copy-Item $src -Destination (Join-Path $installDir 'AvdDexAgent.ps1') -Force

$config = @{
    ServerUrl = $ServerUrl.TrimEnd('/')
    ApiKey    = $ApiKey
    HostPool  = $HostPool
} | ConvertTo-Json
Set-Content -Path (Join-Path $installDir 'agent-config.json') -Value $config -Encoding Ascii

# ------------------------------------------------------------------ task --
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f
    (Join-Path $installDir 'AvdDexAgent.ps1')
)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
    -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false } catch { }
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $taskPrincipal -Settings $settings | Out-Null

Write-Host "AVD DEX agent installed."
Write-Host "  Install dir : $installDir"
Write-Host "  Server      : $($ServerUrl.TrimEnd('/'))"
Write-Host "  Host pool   : $(if ($HostPool) { $HostPool } else { '(not set)' })"
Write-Host "  Interval    : every $IntervalMinutes minute(s), as SYSTEM"
Write-Host ""
Write-Host "Verify: Start-ScheduledTask -TaskName $taskName ; then check"
Write-Host "        $ServerUrl/api/agent/status on the dashboard server."
