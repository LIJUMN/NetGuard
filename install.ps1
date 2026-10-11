<#
.SYNOPSIS
    NetGuard installer.

.DESCRIPTION
    -Download   fetch mihomo + clash-speedtest + geo data into bin/ (once)
    -InstallTasks  register the scheduled tasks and the tray autostart
    -Uninstall    remove the scheduled tasks and the autostart entry
    -Status       show what is currently installed

    Nothing here is hardcoded to a machine: every path derives from the
    repository root, and the Python interpreter is whatever `$env:NETGUARD_PYTHON`
    or `python` resolves to.
#>
param(
    [switch]$Download,
    [switch]$InstallTasks,
    [switch]$Uninstall,
    [switch]$Status,
    [string]$Python = ''
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }

$BinDir  = Join-Path $Root 'bin'
$MihDir  = Join-Path $BinDir 'mihomo'
$CfgDir  = Join-Path $Root 'config'
$LogDir  = Join-Path $Root 'logs'
$SubDir  = Join-Path $CfgDir 'subs'
$Intel   = Join-Path $Root 'intel'
$PS      = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

foreach ($d in @($BinDir, $MihDir, $CfgDir, $LogDir, $SubDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# ---------------------------------------------------------------- helpers

function Say([string]$m, [string]$lvl = 'info') {
    $c = @{ info = '  '; ok = 'ok'; warn = '!!'; err = 'XX' }
    Write-Host ("[{0}] {1}" -f $c[$lvl], $m)
}

# Mirrors for GitHub release assets, tried in order. Mainland China users
# need these; everyone else will simply hit the first one.
$Mirrors = @(
    '',
    'https://gh-proxy.com/',
    'https://ghfast.top/'
)

function Get-Remote([string]$url, [string]$out, [int]$timeoutSec = 600) {
    foreach ($m in $Mirrors) {
        $target = $m + $url
        Say ("fetching " + [IO.Path]::GetFileName($out))
        & curl.exe -L -s -m $timeoutSec -o $out $target 2>&1 | Out-Null
        if ((Test-Path $out) -and (Get-Item $out).Length -gt 1000) { return $true }
    }
    return $false
}

function Expand-To($zip, $dir) {
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, $dir)
        return $true
    } catch {
        Say ("extract failed: " + $_.Exception.Message) 'err'
        return $false
    }
}

# ---------------------------------------------------------------- download

function Install-Binaries {
    Say 'downloading dependencies (about 90 MB, once)' 'info'

    # ---- mihomo core
    $exe = Join-Path $MihDir 'mihomo.exe'
    if (Test-Path $exe) {
        Say ('mihomo already present: {0:N1} MB' -f ((Get-Item $exe).Length / 1MB)) 'ok'
    } else {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/MetaCubeX/mihomo/releases/latest' `
                                  -Headers @{ 'User-Agent' = 'NetGuard' } -TimeoutSec 30
        $tag = $rel.tag_name
        $asset = $rel.assets | Where-Object {
            $_.name -match 'windows-amd64.*\.zip$' -and $_.name -notmatch 'compatible'
        } | Select-Object -First 1
        if (-not $asset) { Say 'could not find a mihomo windows asset' 'err'; return $false }
        Say ("mihomo " + $tag)
        $zip = Join-Path $env:TEMP 'mihomo.zip'
        if (Get-Remote $asset.browser_download_url $zip) {
            if (Expand-To $zip $MihDir) {
                $got = Get-ChildItem $MihDir -Filter '*.exe' | Select-Object -First 1
                if ($got -and $got.Name -ne 'mihomo.exe') {
                    Rename-Item $got.FullName 'mihomo.exe' -Force
                }
                Remove-Item $zip -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # ---- clash-speedtest
    $cst = Join-Path $BinDir 'clash-speedtest.exe'
    if (Test-Path $cst) {
        Say ('clash-speedtest already present: {0:N1} MB' -f ((Get-Item $cst).Length / 1MB)) 'ok'
    } else {
        try {
            $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/faceair/clash-speedtest/releases/latest' `
                                      -Headers @{ 'User-Agent' = 'NetGuard' } -TimeoutSec 30
            $asset = $rel.assets | Where-Object {
                $_.name -match 'windows.*x86_64\.zip$'
            } | Select-Object -First 1
            if ($asset) {
                Say ('clash-speedtest ' + $rel.tag_name)
                $zip = Join-Path $env:TEMP 'cst.zip'
                if (Get-Remote $asset.browser_download_url $zip) {
                    $tmp = Join-Path $env:TEMP 'cst_extract'
                    if (Expand-To $zip $tmp) {
                        $got = Get-ChildItem $tmp -Recurse -Filter 'clash-speedtest.exe' | Select-Object -First 1
                        if ($got) { Copy-Item $got.FullName $cst -Force }
                    }
                    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
                }
            } else {
                Say 'no windows asset for clash-speedtest' 'warn'
            }
        } catch {
            Say ('clash-speedtest lookup failed: ' + $_.Exception.Message) 'warn'
        }
    }

    # ---- geo data
    foreach ($pair in @(@('geoip.dat', 'geoip.dat'), @('geosite.dat', 'geosite.dat'))) {
        $out = Join-Path $MihDir $pair[0]
        if (Test-Path $out) { Say ($pair[0] + ' already present') 'ok'; continue }
        $url = 'https://github.com/MetaCubeX/meta-rules-dat/releases/latest/download/' + $pair[1]
        if (Get-Remote $url $out 300) { Say ($pair[0] + ' ok') 'ok' }
        else { Say ($pair[0] + ' FAILED (rules will not work)') 'warn' }
    }

    Write-Host ''
    Say 'dependencies ready' 'ok'
    return $true
}

# ---------------------------------------------------------------- tasks

$TaskDefs = @(
    @{ Name = 'NetGuard-Core';    When = 'ONLOGON';           Desc = 'start the proxy core' }
    @{ Name = 'NetGuard-Tray';    When = 'ONLOGON';           Desc = 'start the tray light' }
    @{ Name = 'NetGuard-Boot';    When = 'ONLOGON';  Delay = '0003:00'; Desc = 'screen once after logon' }
    @{ Name = 'NetGuard-Nightly'; When = 'DAILY';   Time = '03:30';     Desc = 'nightly full screening' }
    @{ Name = 'NetGuard-Tg';      When = 'DAILY';   Time = '04:30';     Desc = 'Telegram channels -> pool' }
    @{ Name = 'NetGuard-Prune';   When = 'DAILY';   Time = '04:45';     Desc = 'dedupe sources' }
    @{ Name = 'NetGuard-Discover';When = 'DAILY';   Time = '05:15';     Desc = 'hunt new sources' }
)

function Install-Tasks {
    Say 'registering scheduled tasks' 'info

    $runner = Join-Path $Root 'pipeline\run_pipeline.cmd'
    $watch  = Join-Path $Root 'pipeline\proxy-watchdog.ps1'
    $core   = Join-Path $Root 'pipeline\core-ctl.ps1'

    foreach ($t in $TaskDefs) {
        schtasks /Delete /TN $t.Name /F 2>&1 | Out-Null
    }

    $created = 0
    foreach ($t in $TaskDefs) {
        $action = switch ($t.Name) {
            'NetGuard-Core'     { "`"$core`"" }
            'NetGuard-Tray'     { "`"$env:SystemRoot\System32\wscript.exe`" `"$Root\tray\start_tray.vbs`"" }
            'NetGuard-Boot'     { "`"$runner`" -SkipSources -SkipIdleCheck" }
            'NetGuard-Nightly'  { "`"$runner`"" }
            'NetGuard-Tg'       { "`"$PS`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$Python' '$Intel\tg_harvest.py' --seeds '$Root\pipeline\tg_channels.txt'`"" }
            'NetGuard-Prune'    { "`"$PS`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$Python' '$Intel\manage_sources.py' refresh`"" }
            'NetGuard-Discover' { "`"$PS`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\pipeline\discover.ps1`"" }
        }
        $sched = "/SC $($t.When)"
        if ($t.Delay) { $sched += " /DELAY $($t.Delay)" }
        if ($t.Time)  { $sched += " /ST $($t.Time)" }

        $out = schtasks /Create /TN $t.Name /TR $action $sched /F /RL HIGHEST 2>&1
        if ($LASTEXITCODE -eq 0) {
            Say ("{0,-18} {1}" -f $t.Name, $t.Desc) 'ok'
            $created++
        } else {
            Say ("{0,-18} FAILED: {1}" -f $t.Name, ($out | Out-String).Trim()) 'err'
        }
    }

    # 5-minute self-heal
    schtasks /Delete /TN 'NetGuard-Watchdog' /F 2>&1 | Out-Null
    $out = schtasks /Create /TN 'NetGuard-Watchdog' `
        /TR "`"$PS`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watch`"" `
        /SC MINUTE /MO 5 /F /RL HIGHEST 2>&1
    if ($LASTEXITCODE -eq 0) { Say ('NetGuard-Watchdog   self-heal every 5 min') 'ok'; $created++ }

    Write-Host ''
    Say ("$created tasks registered") 'ok'
    Say 'log off and back on to see the whole chain take effect' 'info'
}

function Uninstall-Tasks {
    Say 'removing scheduled tasks' 'info
    foreach ($n in @('NetGuard-Core', 'NetGuard-Tray', 'NetGuard-Boot', 'NetGuard-Nightly',
                     'NetGuard-Tg', 'NetGuard-Prune', 'NetGuard-Discover', 'NetGuard-Watchdog')) {
        schtasks /Delete /TN $n /F 2>&1 | Out-Null
        Say ("$n removed") 'ok'
    }
    Say 'the tray icon can also be removed from the Startup folder' 'info'
}

function Show-Status {
    Write-Host ''
    Say ("repository : " + $Root)
    Say ("python     : " + $Python) 'info'
    Say '--- binaries ---' 'info'
    foreach ($p in @((Join-Path $MihDir 'mihomo.exe'), (Join-Path $BinDir 'clash-speedtest.exe'))) {
        if (Test-Path $p) { Say ("{0,-22} {1:N1} MB" -f (Split-Path $p -Leaf), ((Get-Item $p).Length / 1MB)) 'ok' }
        else { Say ("{0,-22} MISSING" -f (Split-Path $p -Leaf)) 'warn' }
    }
    Say '--- core ---' 'info
    try {
        $v = Invoke-RestMethod 'http://127.0.0.1:9090/version' -TimeoutSec 3
        Say ("mihomo {0} running" -f $v.version) 'ok'
    } catch { Say 'mihomo not running' 'warn' }
    Say '--- tasks ---' 'info
    foreach ($t in $TaskDefs) {
        $r = schtasks /Query /TN $t.Name 2>&1
        if ($LASTEXITCODE -eq 0) { Say ("{0,-18} installed" -f $t.Name) 'ok' }
        else { Say ("{0,-18} -" -f $t.Name) 'warn' }
    }
    Write-Host ''
}

# ---------------------------------------------------------------- main

if (-not $Python) {
    $Python = $env:NETGUARD_PYTHON
    if (-not $Python) {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source }
    }
}
if (-not $Python) {
    Say 'Python not found. Install Python 3.9+ or set $env:NETGUARD_PYTHON.' 'err'
    Say 'The tray alone can run as a standalone EXE without Python.' 'info'
}

$did = $false
if ($Status)    { Show-Status; $did = $true }
if ($Uninstall) { Uninstall-Tasks; $did = $true }
if ($InstallTasks) { Install-Tasks; $did = $true }
if ($Download)  { Install-Binaries | Out-Null; $did = $true }

if (-not $did) {
    Write-Host ''
    Write-Host '  NetGuard installer' 'info'
    Write-Host ''
    Write-Host '  -Download       fetch mihomo + clash-speedtest + geo data (~90 MB)'
    Write-Host '  -InstallTasks   register the nightly chain and tray autostart'
    Write-Host '  -Uninstall      remove every scheduled task'
    Write-Host '  -Status         show what is installed'
    Write-Host ''
    Write-Host '  Typical first run:'
    Write-Host '    .\install.ps1 -Download'
    Write-Host '    .\pipeline\pipeline.ps1'
    Write-Host '    .\install.ps1 -InstallTasks'
    Write-Host ''
}