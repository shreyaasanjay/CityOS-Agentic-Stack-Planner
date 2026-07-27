param(
  [int]$Port = 8788,
  [string]$BindHost = "127.0.0.1",
  [switch]$Lan,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
$Launcher = Join-Path $PSScriptRoot "local-ui\start-runner.ps1"

& $Launcher -Port $Port -BindHost $BindHost -Lan:$Lan -Open:$Open
