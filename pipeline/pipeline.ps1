param(
    [int]$MinSpeedKB = 60,
    [int]$KeepFast   = 12,
    [int]$TopSpeed   = 40,
    [int]$SpeedSec   = 4,
    [int]$SpeedBytes = 1000000,
    [switch]$SkipSources,
    [switch]$SkipIdleCheck,
    [string]$Root = ''
)
$ErrorActionPreference = 'Continue'

# Repo root: one level above this script's folder, unless overridden.
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}
$Root = (Resolve-Path $Root).Path

$CfgDir  = Join-Path $Root 'config'
$LogDir  = Join-Path $Root 'logs'
$Dir     = Join-Path $Root 'pipeline'
$Bin     = Join-Path $Root 'bin'
$Sub     = Join-Path $CfgDir 'subs'
$Intel   = Join-Path $Root 'intel'
$Exe     = Join-Path $Bin 'mihomo\mihomo.exe'
$Tpl     = Join-Path $CfgDir 'config.template.yaml'
$Cfg     = Join-Path $CfgDir 'config.yaml'
$SrcJ    = Join-Path $CfgDir 'sources.json'
$FastIn  = Join-Path $Sub 'nodes_fast.yaml'
$Api     = 'http://127.0.0.1:9090'
$Log     = Join-Path $LogDir 'pipeline.log'
$StateF  = Join-Path $CfgDir 'tray_state.json'
$CST     = Join-Path $Bin 'clash-speedtest.exe'
$Utf8    = New-Object Text.UTF8Encoding($false)

foreach ($d in @($CfgDir, $LogDir, $Sub)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# The interpreter that runs intel/. Override with -Python or $env:NETGUARD_PYTHON.
$py = $env:NETGUARD_PYTHON
if (-not $py) { $py = 'python' }

function Log([string]$m) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

function Set-State($obj) {
    [IO.File]::WriteAllText($StateF, ($obj | ConvertTo-Json -Depth 3), $Utf8)
}

$mtx = New-Object System.Threading.Mutex($false, 'WBPipeline')
if (-not $mtx.WaitOne(0)) { Log 'pipeline already running -> exit'; exit 0 }

# lock file: the tray reads this to show the amber "busy" state.
# Clear a stale one first - if a previous run was killed hard, its finally
# block never ran and the tray would sit amber forever.
$LockF = Join-Path $CfgDir 'pipeline.lock'
if (Test-Path $LockF) {
    $age = ((Get-Date) - (Get-Item $LockF).LastWriteTime).TotalMinutes
    if ($age -gt 30) {
        Log ("removing stale lock ({0:N0} min old)" -f $age)
        Remove-Item $LockF -Force -ErrorAction SilentlyContinue
    }
}
try { [IO.File]::WriteAllText($LockF, (Get-Date).ToString('s'), $Utf8) } catch { }

try {
    $t0 = Get-Date
    Log '=== pipeline start ==='
    Set-State @{ stage = 'harvesting'; alive = 0; total = 0; ts = (Get-Date).ToString('s') }

    # ---------- 0. do not fight the user for bandwidth ----------
    # The nightly and logon runs can land while you are actually online.
    # Sample the core's traffic counters; if the link is moving data, postpone.
    if (-not $SkipIdleCheck) {
        Set-State @{ stage = 'waiting'; msg = 'link busy, waiting for idle'; ts = (Get-Date).ToString('s') }
        $idleOut = & $py (Join-Path $Intel 'idle.py') --wait 18 2>&1
        $idleRc = $LASTEXITCODE
        Log ('idle check: ' + (($idleOut | Select-Object -Last 1)))
        if ($idleRc -ne 0) {
            Log 'link still busy after ~3min -> postpone this run'
            $prev = $null
            try { $prev = Get-Content $StateF -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
            Set-State @{ stage = 'done'; alive = $prev.alive; fast = $prev.fast
                         verify = $prev.verify; msg = 'postponed (link busy)'
                         ts = (Get-Date).ToString('s'); elapsed = 0 }
            exit 0
        }
        Log 'link idle -> proceeding'
    }

    # prune duplicate sources FIRST: every future run loads less for free
    $prune = & $py (Join-Path $Intel 'manage_sources.py') refresh 2>&1
    $prune | ForEach-Object { Log ("  prune: " + $_) }

    # ---------- 1. refresh sources ----------
    # NB: PowerShell 5.1 feeds the whole array as ONE object through the pipe,
    # so ConvertFrom-Json must be assigned FIRST, then wrapped.
    $rawSrc = Get-Content $SrcJ -Raw -Encoding UTF8 | ConvertFrom-Json
    $sources = @($rawSrc)
    if (-not $SkipSources) {
        $mirrors = @('https://gh-proxy.com/', 'https://ghfast.top/', 'https://ghproxy.net/')
        foreach ($s in $sources) {
            if (-not $s.enabled) { continue }
            $out = Join-Path $Sub ($s.name + '.yaml')
            $got = $false
            foreach ($m in $mirrors) {
                $null = & curl.exe -L -s -m 40 -o $out -w "%{http_code}" ($m + $s.url) 2>&1
                if ((Test-Path $out) -and (Get-Item $out).Length -gt 3000) { $got = $true; break }
            }
            if ($got) { $s.fails = 0 }
            else {
                $s.fails = [int]$s.fails + 1
                if ($s.fails -ge 5) { $s.enabled = $false; Log ("source disabled: " + $s.name) }
            }
        }
        [IO.File]::WriteAllText($SrcJ, ($sources | ConvertTo-Json -Depth 4), $Utf8)
    }
    $active = @($sources | Where-Object { $_.enabled })
    Log ("sources active = {0}" -f $active.Count)

    # ---------- 2. rebuild config ----------
    $prov = ''; $use = ''
    foreach ($s in $active) {
        $prov += "  $($s.name):`n    type: file`n    path: `"./subs/$($s.name).yaml`"`n"
        $prov += "    health-check:`n      enable: true`n      url: https://www.google.com/generate_204`n      interval: 300`n"
        $use  += "      - $($s.name)`n"
    }
    $cfgText = [IO.File]::ReadAllText($Tpl, [Text.Encoding]::UTF8).
        Replace('{{PROVIDERS}}', $prov.TrimEnd("`n")).
        Replace('{{PROVIDER_USE}}', $use.TrimEnd("`n"))
    [IO.File]::WriteAllText($Cfg, $cfgText, $Utf8)

    $t = & $Exe -d $Dir -f $Cfg -t 2>&1 | Select-Object -Last 1
    if ("$t" -notmatch 'successful') { Log ("ABORT config invalid: $t"); Set-State @{ stage='error'; msg="$t" }; exit 1 }
    Log 'config valid'

    # ---------- 3. reload ----------
    [IO.File]::WriteAllText((Join-Path $LogDir 'reload.json'),
        '{"path":"' + ($Cfg -replace '\\','/') + '"}', $Utf8)
    $null = & curl.exe -s -X PUT -H "Content-Type: application/json" `
        --data-binary "@$(Join-Path $LogDir 'reload.json')" "$Api/configs?force=true" 2>&1
    Log 'reloaded; waiting for AUTO group to become ready'
    $ready = $false
    $readyJ = Join-Path $LogDir 'ready.json'
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 5
        $null = & curl.exe -s -o $readyJ -m 20 "$Api/proxies/AUTO" 2>&1
        try {
            $a = [IO.File]::ReadAllText($readyJ, [Text.Encoding]::UTF8) | ConvertFrom-Json
            $cnt = @($a.all).Count
            if ($cnt -gt 20) { $ready = $true; Log ("AUTO ready: {0} options after {1}s" -f $cnt, ($i + 1) * 5); break }
        } catch { }
    }
    if (-not $ready) { Log 'WARN AUTO not ready after 150s, probing anyway' }

    # ---------- 4. stage-1 latency screen (curl -> file, avoids PS encoding bug) ----------
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $rawJ = Join-Path $LogDir 'alive_raw.json'
    $null = & curl.exe -s -o $rawJ -m 120 "$Api/group/AUTO/delay?url=https%3A%2F%2Fwww.google.com%2Fgenerate_204&timeout=5000" 2>&1
    $txt = [IO.File]::ReadAllText($rawJ, [Text.Encoding]::UTF8)
    $d = $txt | ConvertFrom-Json
    $sw.Stop()
    $names = @($d.PSObject.Properties |
        Where-Object { "$($_.Value)" -match '^\d+$' } |
        Sort-Object { [int]$_.Value } |
        ForEach-Object { $_.Name })
    Log ("stage-1 latency {0:N1}s  alive={1}" -f $sw.Elapsed.TotalSeconds, $names.Count)
    if ($names.Count -eq 0) { Log 'ABORT no live node'; Set-State @{stage='error'; msg='no live node'}; exit 1 }
    $extract = Join-Path $Intel 'extract_fast.py'
    $namesFile = Join-Path $LogDir 'alive_names.json'
    $avail = Join-Path $LogDir 'available.yaml'
    # only the top-N by latency go to the (slow) bandwidth stage
    [IO.File]::WriteAllText($namesFile, (@($names) | ConvertTo-Json -Depth 2), $Utf8)
    # credit every source for what it actually delivered (before renaming)
    $ry = & $py (Join-Path $Intel 'record_yield.py') $namesFile (Get-Date -Format 'HH:mm') 2>&1
    $ry | ForEach-Object { Log ("  yield: " + $_) }

    $cand = @($names | Select-Object -First ([Math]::Min($TopSpeed, $names.Count)))
    Log ("stage-2 testing top {0} of {1} by latency" -f $cand.Count, $names.Count)
    [IO.File]::WriteAllText($namesFile, (@($cand) | ConvertTo-Json -Depth 2), $Utf8)

    # ---------- 5. extract full definitions ----------
    $pyOut = & $py $extract $namesFile $avail 2>&1
    $picked = ($pyOut | Select-String 'picked').ToString()
    Log ("stage-2 extract: " + $picked)

    # ---------- 6. stage-3 real bandwidth via clash-speedtest ----------
    $fastYaml = Join-Path $LogDir 'fast.yaml'
    Remove-Item $fastYaml -Force -ErrorAction SilentlyContinue
    $sw2 = [Diagnostics.Stopwatch]::StartNew()
    & $CST -c $avail -concurrent 12 -max-latency 6s -timeout 4s `
        -min-download-speed ([double]$MinSpeedKB / 1024) `
        -download-size $SpeedBytes -output $fastYaml *> (Join-Path $LogDir 'cst_log.txt')
    $sw2.Stop()
    Log ("stage-3 speedtest {0:N1}s" -f $sw2.Elapsed.TotalSeconds)

    $passed = 0
    if (Test-Path $fastYaml) {
        $ftxt = [IO.File]::ReadAllText($fastYaml, [Text.Encoding]::UTF8)
        $passed = ([regex]::Matches($ftxt, '(?m)^\s*-\s+name')).Count
        # clash-speedtest emits a full config (proxy-providers + proxies).
        # mihomo's proxy-provider refuses it -> strip to proxies only.
        $clean = Join-Path $Intel 'clean_fast.py'
        & $py $clean $fastYaml $FastIn 2>&1 | ForEach-Object { Log ("  clean: " + $_) }
    }
    Log ("stage-3 passed = {0}" -f $passed)

    # feed results back into the reputation library
    if ($passed -gt 0 -and (Test-Path $avail)) {
        $sr = & $py (Join-Path $Intel 'speed_result.py') $avail $fastYaml (Get-Date -Format 'HH:mm') 2>&1
        $sr | ForEach-Object { Log ("  reputation: " + $_) }
    } else {
        Log 'reputation: skipped (no node passed the speed filter)'
    }

    if ($passed -lt 1) { Log 'WARN no node passed speed filter'; }

    # ---------- 7. reload with the fast set ----------
    $t2 = & $Exe -d $Dir -f $Cfg -t 2>&1 | Select-Object -Last 1
    if ("$t2" -match 'successful') {
        $null = & curl.exe -s -X PUT -H "Content-Type: application/json" `
            --data-binary "@$(Join-Path $LogDir 'reload.json')" "$Api/configs?force=true" 2>&1
        Start-Sleep -Seconds 6
        Log 'reloaded with fast set'
    } else { Log "reload skipped: $t2" }

    # ---------- 8. verify ----------
    $ok = 0
    foreach ($u in @('https://www.google.com/generate_204', 'https://github.com')) {
        $code = & curl.exe -s -x http://127.0.0.1:7890 -o NUL -m 20 -w "%{http_code}" $u 2>&1
        if ("$code" -ne '000') { $ok++ }
    }
    $elapsed = ((Get-Date) - $t0).TotalSeconds
    Log ("=== pipeline done: alive={0} passed={1} verify={2}/2 in {3:N0}s ===" -f $names.Count, $passed, $ok, $elapsed)
    Set-State @{ stage = 'done'; alive = $names.Count; fast = $passed;
                 verify = $ok; total = 0; ts = (Get-Date).ToString('s');
                 elapsed = [math]::Round($elapsed) }
}
finally {
    Remove-Item $LockF -Force -ErrorAction SilentlyContinue
    $mtx.ReleaseMutex(); $mtx.Dispose()
}
