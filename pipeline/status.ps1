param([string]$Root = '')
$ErrorActionPreference = 'Continue'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$api = 'http://127.0.0.1:9090'

Write-Output '================ PROXY STATUS ================'

$v = $null
try { $v = Invoke-RestMethod "$api/version" -TimeoutSec 3 } catch { }
if ($v) {
    Write-Output ("core        : RUNNING  (mihomo {0})" -f $v.version)
} else {
    Write-Output 'core        : DOWN  <-- run proxy-watchdog.ps1'
}

$p = Get-Process mihomo -ErrorAction SilentlyContinue
if ($p) { Write-Output ("process     : PID {0}  window={1}" -f $p[0].Id, $p[0].MainWindowHandle) }

$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
          Where-Object { $_.LocalPort -in 7890, 7891, 9090 } | Select-Object -ExpandProperty LocalPort -Unique
Write-Output ("listening   : {0}" -f (($listen | Sort-Object) -join ', '))

$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$cur = Get-ItemProperty -Path $k -ErrorAction SilentlyContinue
Write-Output ("system proxy: Enable={0} Server={1}" -f $cur.ProxyEnable, $cur.ProxyServer)

if ($v) {
    foreach ($g in @('PROXY','AUTO','FALLBACK')) {
        try {
            $gd = Invoke-RestMethod "$api/proxies/$g" -TimeoutSec 5
            Write-Output ("group {0,-9}: now = {1}" -f $g, $gd.now)
        } catch { }
    }
    try {
        $pv = Invoke-RestMethod "$api/providers/proxies" -TimeoutSec 15
        $tot = 0
        foreach ($n in $pv.providers.PSObject.Properties.Name) {
            if ($pv.providers.$n.proxies) { $tot += $pv.providers.$n.proxies.Count }
        }
        Write-Output ("total nodes : {0}" -f $tot)
    } catch { }
}

Write-Output '---------------- connectivity ----------------'
$tests = @(
  @{ u = 'https://www.google.com/generate_204'; n = 'Google  ' },
  @{ u = 'https://www.youtube.com';             n = 'YouTube ' },
  @{ u = 'https://github.com';                  n = 'GitHub  ' },
  @{ u = 'https://www.baidu.com';               n = 'Baidu   ' }
)
foreach ($t in $tests) {
    $r = & curl.exe -s -x http://127.0.0.1:7890 -o NUL -m 15 -w "%{http_code}|%{time_total}s" $t.u 2>&1
    $parts = "$r" -split '\|'
    $tag = if ($parts[0] -eq '000') { 'FAIL' } else { 'OK  ' }
    Write-Output ("{0} {2}  HTTP {1}" -f $t.n, $parts[0], $tag)
}
Write-Output '=============================================='