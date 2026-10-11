param([string]$Root = '')
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path $Root).Path

$Dir = Join-Path $Root 'bin\mihomo'
$Ctl = Join-Path $Root 'pipeline\core-ctl.ps1'
$log = Join-Path $Root 'logs\watchdog.log'
$api = 'http://127.0.0.1:9090'
if (-not (Test-Path (Split-Path $log))) { New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null }

function Write-Log([string]$m) {
    Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8
}

function Test-Core {
    try {
        $null = Invoke-RestMethod "$api/version" -TimeoutSec 3
        return $true
    } catch { return $false }
}

$ok = Test-Core
if (-not $ok) {
    Write-Log 'core down -> restarting'
    Get-Process mihomo -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    & powershell -NoProfile -ExecutionPolicy Bypass -File $Ctl | Out-Null
    Start-Sleep -Seconds 2
    if (Test-Core) { Write-Log 'core restarted OK' } else { Write-Log 'core restart FAILED' }
}

$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
          Where-Object { $_.LocalPort -in 7890, 9090 }
if (-not $listen) { Write-Log 'WARN: proxy ports not listening' }

$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$cur = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
if ($cur.ProxyEnable -ne 1 -or $cur.ProxyServer -ne '127.0.0.1:7890') {
    Set-ItemProperty -Path $k -Name ProxyEnable -Value 1 -Type DWord
    Set-ItemProperty -Path $k -Name ProxyServer -Value '127.0.0.1:7890'
    Write-Log 'system proxy re-asserted'
}

# keep watchdog log from growing forever
try {
    $fi = Get-Item $log -ErrorAction SilentlyContinue
    if ($fi -and $fi.Length -gt 512KB) {
        $tail = Get-Content $log -Tail 400
        [IO.File]::WriteAllLines($log, $tail, (New-Object Text.UTF8Encoding($false)))
    }
} catch { }
