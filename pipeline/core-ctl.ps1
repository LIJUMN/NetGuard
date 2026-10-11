param(
    [string]$Root = '',
    [string]$ExeName = 'mihomo.exe',
    [string]$CfgName = 'config.yaml',
    [string]$LogName = 'mihomo.log',
    [switch]$Restart
)
$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path $Root).Path
$Dir = Join-Path $Root 'bin\mihomo'
$exe = Join-Path $Dir $ExeName
$cfg = Join-Path $Root ('config\' + $CfgName)
$log = Join-Path $Root ('logs\' + $LogName)
foreach ($d in @((Split-Path $log), $Dir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

if ($Restart) {
    Get-Process -Name 'mihomo' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Write-Output 'old core stopped'
}

$inner = 'cmd.exe /c ""' + $exe + '" -d "' + $Dir + '" -f "' + $cfg + '" >> "' + $log + '" 2>&1"'
$mc = New-Object System.Management.ManagementClass('Win32_Process')
$in = $mc.GetMethodParameters('Create')
$in['CommandLine'] = $inner
$si = ([wmiclass]'Win32_ProcessStartup').CreateInstance()
$si.ShowWindow = 0
$in['ProcessStartupInformation'] = $si
$out = $mc.InvokeMethod('Create', $in, $null)
Write-Output ("WMI ReturnValue={0} PID={1}" -f $out['ReturnValue'], $out['ProcessId'])

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $v = Invoke-RestMethod -Uri 'http://127.0.0.1:9090/version' -TimeoutSec 2
        Write-Output ("API ready: version={0}  after {1:N1}s" -f $v.version, ($i * 0.5 + 0.5))
        $ok = $true
        break
    } catch { }
}
if (-not $ok) { Write-Output 'API NOT READY after 15s'; exit 1 }
$p = Get-Process -Name 'mihomo' -ErrorAction SilentlyContinue
if ($p) { Write-Output ("MainWindowHandle={0} (0 means hidden, good)" -f $p[0].MainWindowHandle) }
