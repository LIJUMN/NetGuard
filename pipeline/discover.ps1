param(
    [int]$PerQuery   = 30,
    [int]$MinStars   = 3,
    [int]$FreshDays  = 45,
    [switch]$DryRun,
    [string]$Root = ''
)
$ErrorActionPreference = 'Continue'

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path $Root).Path

$CfgDir = Join-Path $Root 'config'
$LogDir = Join-Path $Root 'logs'
$Intel  = Join-Path $Root 'intel'
$SubDir = Join-Path $CfgDir 'subs'
$SrcJ   = Join-Path $CfgDir 'sources.json'
$Log    = Join-Path $LogDir 'discover.log'
$Tmp    = Join-Path $env:TEMP 'netguard_discover'
$Utf8   = New-Object Text.UTF8Encoding($false)
foreach ($d in @($CfgDir, $LogDir, $SubDir, $Tmp)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

function Log([string]$m) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

# ---- GitHub token: environment variable ONLY.
# Never read a token from a file on disk, and never commit one.
$Token = $env:GITHUB_TOKEN
if (-not $Token) { Log 'no GITHUB_TOKEN set - using unauthenticated API (60 req/h)'; }
else { Log ("using GITHUB_TOKEN ({0} chars)" -f $Token.Length) }

$hdrs = @('-H', 'Accept: application/vnd.github+json')
if ($Token) { $hdrs += @('-H', "Authorization: token $Token") }

$py = $env:NETGUARD_PYTHON
if (-not $py) { $py = 'python' }

$mtx = New-Object System.Threading.Mutex($false, 'WBNodeDiscover')
if (-not $mtx.WaitOne(0)) { Log 'discover already running -> exit'; exit 0 }

try {
    Log '=== discover start ==='
    $since = (Get-Date).AddDays(-$FreshDays).ToString('yyyy-MM-dd')

    $rawSrc = Get-Content $SrcJ -Raw -Encoding UTF8 | ConvertFrom-Json
    $sources = @($rawSrc)
    $known = @{}
    foreach ($s in $sources) { $known[$s.url] = $true }
    Log ("existing sources = {0}" -f $sources.Count)

    $queries = @(
        ("clash " + [char]0x8BA2 + [char]0x9605 + " in:name,description,readme"),
        'clash subscribe in:name,description',
        'free clash nodes in:name,description,readme',
        'clash yaml nodes in:name,description',
        'v2ray subscribe in:name,description',
        'proxy subscribe clash in:name,description',
        ([char]0x8282 + [char]0x70B9 + " " + [char]0x5206 + [char]0x4EAB + " " + [char]0x8BA2 + [char]0x9605 + " in:name,description,readme"),
        'clash config free in:name,description',
        'shadowsocks subscription in:name,description',
        'airport subscribe in:name,description'
    )

    $repos = @{}
    foreach ($q in $queries) {
        $full = "$q pushed:>$since"
        $enc = [Uri]::EscapeDataString($full)
        $url = "https://api.github.com/search/repositories?q=$enc&sort=stars&order=desc&per_page=$PerQuery"
        & curl.exe -s -m 30 @hdrs -o (Join-Path $Tmp 'srch.json') $url 2>&1 | Out-Null
        $j = $null
        try { $j = Get-Content (Join-Path $Tmp 'srch.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        if (-not $j -or -not $j.items) { Log ("  query failed: $q"); Start-Sleep -Seconds 3; continue }
        $n = 0
        foreach ($it in $j.items) {
            $fn = $it.full_name
            if (-not $repos.ContainsKey($fn)) {
                $repos[$fn] = [pscustomobject]@{
                    full_name = $fn
                    branch = $it.default_branch
                    stars = $it.stargazers_count
                    pushed = $it.pushed_at
                }
                $n++
            }
        }
        Log ("  [query] {0,-46} new repos={1}" -f $q, $n)
        Start-Sleep -Seconds 3
    }
    Log ("total unique repos discovered = {0}" -f $repos.Count)

    # ---- probe common subscription paths ----
    $candPaths = @(
        'clash.yaml','clash.yml','Clash.yaml','Clash.yml',
        'sub/clash.yaml','sub/clash.yml','subscribe/clash.yaml','subscribe/clash.yml',
        'list.yml','list.yaml','all.yml','all.yaml','Eternity.yml','Eternity.yaml',
        'sub/sub_merge_yaml.yml','sub/sub_merge.yml','clash/config.yaml',
        'node.yaml','node.yml','nodes.yaml','nodes.yml',
        'sub/','clash','list','sub'
    )
    $mirrors = @('https://gh-proxy.com/','https://ghfast.top/')
    $newFound = New-Object System.Collections.ArrayList
    $probed = 0
    foreach ($fn in $repos.Keys) {
        $r = $repos[$fn]
        foreach ($p in $candPaths) {
            $raw = "https://raw.githubusercontent.com/$fn/$($r.branch)/$p"
            if ($known.ContainsKey($raw)) { continue }
            $probed++
            $local = Join-Path $Tmp 'probe.bin'
            $ok = $false
            foreach ($m in $mirrors) {
                $null = & curl.exe -L -s -m 25 -o $local -w "%{http_code}" ($m + $raw) 2>&1
                if ((Test-Path $local) -and (Get-Item $local).Length -gt 3000) { $ok = $true; break }
            }
            if (-not $ok) { continue }

            # validate: clash yaml with proxies, or base64 subscription
            $txt = [IO.File]::ReadAllText($local)
            $valid = ($txt -match '(?m)^\s*proxies\s*:')
            if (-not $valid) {
                try {
                    $dec = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($txt -replace '\s','')))
                    if ($dec -match '(vmess|vless|ss|trojan|hysteria2?)://') { $valid = $true }
                } catch { }
            }
            if (-not $valid) { continue }

            $name = ($fn -split '/')[1] + '_' + ([IO.Path]::GetFileNameWithoutExtension($p))
            $name = ($name -replace '[^A-Za-z0-9_]','_')
            [void]$newFound.Add([pscustomobject]@{
                name = $name; url = $raw; enabled = $true; fails = 0
                added = (Get-Date -Format 'yyyy-MM-dd'); stars = $r.stars; hits = 0; alive = 0
            })
            Log ("  FOUND {0,6}* {1}" -f $r.stars, $raw)
            break
        }
    }
    Log ("probed {0} urls, new valid sources = {1}" -f $probed, $newFound.Count)

    if (-not $DryRun -and $newFound.Count -gt 0) {
        # every candidate goes through the SAME overlap gate the Telegram
        # harvester uses - otherwise discover becomes the thing that bloats
        # the library with re-posts.
        $admit = Join-Path $Intel 'manage_sources.py'
        $subDir = $SubDir
        $ok = 0
        foreach ($nf in $newFound) {
            $tmpYaml = Join-Path $subDir ($nf.name + '.yaml')
            $r = & $py $admit admit $nf.name $tmpYaml 2>&1
            $r | ForEach-Object { Log ("  admit " + $nf.name + ": " + $_) }
            if ("$r" -match 'admitted=True') { $ok++ } else { Remove-Item $tmpYaml -Force -ErrorAction SilentlyContinue }
        }
        Log ("admitted {0} / {1} candidates" -f $ok, $newFound.Count)
    }
    Log '=== discover done ==='
}
finally {
    $mtx.ReleaseMutex()
    $mtx.Dispose()
}
