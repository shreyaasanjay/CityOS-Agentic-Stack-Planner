param(
  [int]$Port = 8788,
  [string]$BindHost = "127.0.0.1",
  [switch]$Lan,
  [int]$StartupTimeoutSeconds = 15,
  [switch]$Open
)

$ErrorActionPreference = "Stop"
if ($Lan) {
  $BindHost = "0.0.0.0"
}

function Get-LanAddress {
  $virtualPattern = "vEthernet|WSL|Docker|Loopback|Hyper-V|VirtualBox|VMware"
  $configs = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object {
      $_.IPv4DefaultGateway -and $_.IPv4Address -and
      ($_.InterfaceAlias -notmatch $virtualPattern) -and
      ($_.InterfaceDescription -notmatch $virtualPattern)
    })
  if ($configs.Count -gt 0) { return @($configs[0].IPv4Address)[0].IPAddress }

  $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
      $_.IPAddress -notlike "127.*" -and
      $_.IPAddress -notlike "169.254.*" -and
      $_.InterfaceAlias -notmatch $virtualPattern
    })
  if ($addresses.Count -gt 0) { return $addresses[0].IPAddress }
  return $null
}

function Stop-StaleServer {
  param(
    [string]$PidFile,
    [int]$Port
  )
  if (Test-Path $PidFile) {
    $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($OldPid) {
      $OldPidInt = [int]$OldPid
      $OldPidOnPort = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -eq $OldPidInt }
      if ($OldPidOnPort) {
        Stop-Process -Id $OldPidInt -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped stored PID $OldPid"
      } else {
        Write-Host "Stored PID $OldPid was not listening on port $Port"
      }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }

  $Listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($Listening) {
    foreach ($conn in $Listening) {
      Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
      Write-Host "Cleared stale process $($conn.OwningProcess) on port $Port"
    }
    Start-Sleep -Milliseconds 700
  }

  $StillListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($StillListening) {
    throw "Port $Port is still in use after kill attempts. Manual intervention required."
  }
}

function Wait-ServerBind {
  param(
    [System.Diagnostics.Process]$Process,
    [int]$Port,
    [int]$TimeoutSeconds,
    [string]$ErrLog
  )
  $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $Bound = $null
  do {
    $Bound = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($Bound) { break }
    $Process.Refresh()
    if ($Process.HasExited) { break }
    Start-Sleep -Milliseconds 300
  } while ((Get-Date) -lt $Deadline)

  if ($Bound) {
    $Owner = @($Bound)[0].OwningProcess
    Write-Host "Server confirmed listening on port $Port (PID $Owner)" -ForegroundColor Green
  } else {
    Write-Warning "Server did not bind to port $Port within ${TimeoutSeconds}s. Check $ErrLog for errors."
    if (Test-Path $ErrLog) {
      Write-Host "--- last 20 lines of stderr ---"
      Get-Content $ErrLog -Tail 20
    }
  }
}

function Assert-RunnerSource {
  param(
    [System.Diagnostics.Process]$Process,
    [string]$RepoRoot,
    [int]$Port,
    [string]$PidFile
  )

  $ExpectedRoot = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
  $ExpectedStatic = [System.IO.Path]::GetFullPath(
    (Join-Path $ExpectedRoot "tracefix\runner_ui\static")
  ).TrimEnd('\')

  try {
    $Info = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ui-info" -TimeoutSec 5
  } catch {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    throw "Runner bound to port $Port but /api/ui-info could not verify its source checkout: $($_.Exception.Message)"
  }

  $ActualRoot = [System.IO.Path]::GetFullPath([string]$Info.repo_root).TrimEnd('\')
  $ActualStatic = [System.IO.Path]::GetFullPath([string]$Info.static_dir).TrimEnd('\')
  $RootMatches = [string]::Equals($ActualRoot, $ExpectedRoot, [System.StringComparison]::OrdinalIgnoreCase)
  $StaticMatches = [string]::Equals($ActualStatic, $ExpectedStatic, [System.StringComparison]::OrdinalIgnoreCase)

  if (-not $RootMatches -or -not $StaticMatches) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    throw @"
Runner imported UI code from the wrong checkout.
Expected repo root: $ExpectedRoot
Actual repo root:   $ActualRoot
Expected static:    $ExpectedStatic
Actual static:      $ActualStatic
"@
  }

  Write-Host "Runner source verified: $ExpectedStatic" -ForegroundColor Green
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StateDir = Join-Path $RepoRoot ".tracefix-ui"
$LogDir = Join-Path $StateDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$PidFile = Join-Path $StateDir "runner.pid"
Stop-StaleServer -PidFile $PidFile -Port $Port

$DefaultPython = "C:\Users\razer\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not $env:TRACEFIX_PYTHON_EXE -and (Test-Path -LiteralPath $DefaultPython)) {
  $env:TRACEFIX_PYTHON_EXE = $DefaultPython
}

$RunnerPython = if ($env:TRACEFIX_PYTHON_EXE) { $env:TRACEFIX_PYTHON_EXE } else { "python" }
if ($RunnerPython -ne "python" -and -not (Test-Path -LiteralPath $RunnerPython)) {
  throw "Configured TRACEFIX_PYTHON_EXE does not exist: $RunnerPython"
}

$DefaultJava = "C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot\bin\java.exe"
if ((Test-Path -LiteralPath $DefaultJava)) {
  if (-not $env:TLA_VERIFY_JAVA) { $env:TLA_VERIFY_JAVA = $DefaultJava }
  if (-not $env:JAVA_EXE) { $env:JAVA_EXE = $DefaultJava }
  if (-not $env:JAVA_HOME) { $env:JAVA_HOME = Split-Path -Parent (Split-Path -Parent $DefaultJava) }
  $JavaBin = Split-Path -Parent $DefaultJava
  $PathParts = @($env:PATH -split ';' | Where-Object { $_ })
  if ($PathParts -notcontains $JavaBin) { $env:PATH = $JavaBin + ';' + $env:PATH }
}

$OutLog = Join-Path $LogDir "runner.out.log"
$ErrLog = Join-Path $LogDir "runner.err.log"
$OriginalPythonPath = $env:PYTHONPATH
try {
  $env:PYTHONPATH = if ($OriginalPythonPath) {
    "$RepoRoot;$OriginalPythonPath"
  } else {
    "$RepoRoot"
  }
  $QuotedRepoRoot = '"' + [string]$RepoRoot + '"'
  $Process = Start-Process -FilePath $RunnerPython `
    -ArgumentList @(
      "-B", "-m", "tracefix.runner_ui",
      "--host", "$BindHost",
      "--port", "$Port",
      "--root", $QuotedRepoRoot
    ) `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru
} finally {
  if ($null -eq $OriginalPythonPath) {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
  } else {
    $env:PYTHONPATH = $OriginalPythonPath
  }
}

Set-Content -Path $PidFile -Value $Process.Id
$LocalUrl = "http://127.0.0.1:$Port/"
$LaunchUrl = if ($BindHost -eq "0.0.0.0") { $LocalUrl } else { "http://${BindHost}:$Port/" }
$LanAddress = if ($BindHost -eq "0.0.0.0") { Get-LanAddress } else { $null }
Write-Host "TraceFix LLM runner local: $LocalUrl"
if ($LanAddress) { Write-Host "TraceFix LLM runner LAN  : http://${LanAddress}:$Port/" }
elseif ($BindHost -ne "127.0.0.1") { Write-Host "TraceFix LLM runner bind : http://${BindHost}:$Port/" }
Write-Host "TraceFix bind host: $BindHost"
Write-Host "TraceFix UI Python: $RunnerPython"
Write-Host "TraceFix subprocess Python: $($env:TRACEFIX_PYTHON_EXE)"
Write-Host "TraceFix TLA_VERIFY_JAVA: $($env:TLA_VERIFY_JAVA)"
Write-Host "TraceFix JAVA_EXE: $($env:JAVA_EXE)"
Write-Host "TraceFix JAVA_HOME: $($env:JAVA_HOME)"
Write-Host "PID: $($Process.Id)"
Write-Host "Logs: $OutLog"
Write-Host "      $ErrLog"

Wait-ServerBind -Process $Process -Port $Port -TimeoutSeconds $StartupTimeoutSeconds -ErrLog $ErrLog
Assert-RunnerSource -Process $Process -RepoRoot $RepoRoot -Port $Port -PidFile $PidFile

if ($Open) {
  Start-Process $LaunchUrl
}
