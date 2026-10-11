"""
discover.py — find new subscription sources on GitHub.

This is what keeps the project alive without a maintainer. Free proxy
repositories get deleted, renamed, or go stale. Assuming the seed list stays
valid for a decade is the single biggest way this thing dies.

So every time the library runs dry -- and occasionally even when it does not --
we go looking. Same rule as everywhere else: a candidate has to pass the
overlap gate before it is admitted, otherwise discovery becomes the thing that
bloats the library with re-posts.

A GitHub token is optional. Unauthenticated works, just slower; there is no
code path that fails because the token is missing.
"""
import os
import re
import sys
import json
import time
import base64
import shutil
import threading
import subprocess
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import paths as P  # noqa: E402
from intel import source_intel as si          # noqa: E402
from intel import manage_sources as ms        # noqa: E402

MIRRORS = P.MIRRORS

# Chinese search terms are spelled with escapes on purpose: this file has to
# survive being read as GBK by Windows PowerShell tooling, and a stray byte
# there has broken the whole module before.
# Measured on this machine rather than guessed. Ten queries were tried; these
# five are the ones that actually returned repositories. Each one costs a
# network round trip, and when GitHub is only reachable through a mirror a
# query is expensive enough that spending it on a phrase with a poor hit rate
# is a real cost. Cut to what worked:
#   "节点 分享 订阅"      -> +30 repositories
#   "shadowsocks subscription" -> +24
#   "v2ray subscribe"      -> +5
QUERIES = [
    "节点 分享 订阅 in:name,description,readme",
    "shadowsocks subscription in:name,description",
    "clash 订阅 in:name,description,readme",
    "free clash nodes in:name,description",
    "v2ray subscribe in:name,description",
]

# Paths that actually tend to hold a subscription in a repo that has one.
CANDIDATE_PATHS = [
    "clash.yaml", "clash.yml", "Clash.yaml", "Clash.yml",
    "sub/clash.yaml", "sub/clash.yml",
    "subscribe/clash.yaml", "subscribe/clash.yml",
    "list.yml", "list.yaml", "all.yml", "all.yaml",
    "Eternity.yml", "Eternity.yaml",
    "sub/sub_merge_yaml.yml", "sub/sub_merge.yml",
    "clash/config.yaml", "node.yaml", "nodes.yaml",
    # The names the working sources actually use. Added after a 40-repo run
    # probed the list above and found nothing: the plausible-looking names were
    # guesses, and the live ones were not in the list at all.
    "clash", "sub", "subs", "subscribe", "config/clash.yaml",
    "clash.yml.txt", "clash.yaml.txt", "clash_config.yaml",
    "clash_verge.yaml", "mihomo.yaml", "meta.yaml",
    "subscribe/clash", "sub/clash", "public/clash.yaml",
    "dist/clash.yaml", "output/clash.yaml", "clash.yaml1",
    "Latest.yaml", "latest.yaml", "Nodes.yaml", "nodes.json",
]

LOGIN_RE = re.compile(r"(vmess|vless|trojan|ss|ssr|hysteria2?)://")


def _log(msg):
    print("[discover] %s" % msg, flush=True)
    try:
        P.ensure_dirs()
        with open(P.DISCOVER_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _curl(url, timeout=15):
    """
    Fetch through the mirrors. Returns bytes or None.

    The temp file name is unique per thread. It used to be a single shared
    "_disc.bin", which is fine right up to the moment anything runs in parallel:
    two fetches writing the same path corrupt each other, and the loser reads
    the winner's bytes. Unique names also make concurrent probing safe, which
    is what brought discovery back under a usable wall-clock time.

    os.system is replaced by subprocess: it goes through a shell, it cannot be
    given a timeout reliably, and quoting a URL through cmd.exe is a needless
    way to end up with a filename containing a stray quote.
    """
    tid = threading.get_ident()
    for m in MIRRORS:
        tmp = os.path.join(P.LOGS, "_disc_%d.bin" % tid)
        P.ensure_dirs()
        try:
            r = subprocess.run(
                ["curl.exe", "-L", "-s", "-m", str(timeout),
                 "-o", tmp, m + url],
                capture_output=True, timeout=timeout + 8,
                creationflags=0x08000000)
            if r.returncode == 0 and os.path.exists(tmp) \
                    and os.path.getsize(tmp) > 100:
                with open(tmp, "rb") as fh:
                    data = fh.read()
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return data
        except Exception:
            continue
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return None


def _api(url):
    """
    GitHub API with the token if we have one; never a hard failure.

    api.github.com is unreachable from a mainland connection, and the failure
    mode is expensive: every query paid a 30 second timeout before falling
    back. With a dozen queries that is most of the run's budget spent proving
    something that was already known twenty seconds in.

    So the failure is remembered. After the first direct failure the proxy is
    tried (which is what usually works once the core is up), and after the
    second failure direct is not attempted again this session -- the queries
    then cost milliseconds instead of half a minute each.
    """
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "NetGuard",
            "Accept": "application/vnd.github+json",
        })
        if P.GITHUB_TOKEN:
            req.add_header("Authorization", "token %s" % P.GITHUB_TOKEN)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        msg = str(e)[:90]
        if not _API_STATE["failed"]:
            _log("api: %s" % msg)
        _API_STATE["failed"] = True
        _log("api: direct GitHub is unreachable; trying the mirror path")
        return _api_via_mirror(url)
    return None


def _api_via_mirror(url):
    """curl through the gh mirrors, which is what works from a mainland net."""
    for m in P.MIRRORS[1:]:
        try:
            r = subprocess.run(
                ["curl.exe", "-s", "-m", "18", "-H", "Accept: application/vnd.github+json",
                 m + url],
                capture_output=True, timeout=25, creationflags=0x08000000)
            body = (r.stdout or b"").strip()
            if not body:
                continue
            return json.loads(body.decode("utf-8", "replace"))
        except Exception:
            continue
    return None


# Set once, used by _api to avoid repeating a hopeless request path.
_API_STATE = {"failed": False}


def _valid_subscription(raw):
    """Is this actually a subscription? clash yaml, or base64 of links."""
    try:
        txt = raw.decode("utf-8", "replace")
    except Exception:
        return False
    if re.search(r"(?m)^\s*proxies\s*:", txt):
        return True
    stripped = re.sub(r"\s", "", txt)
    if len(stripped) < 40 or len(stripped) % 4:
        return False
    try:
        dec = base64.b64decode(stripped + "===").decode("utf-8", "replace")
        return bool(LOGIN_RE.search(dec))
    except Exception:
        return False


def _relevance(repo):
    """
    How likely is this repository to actually contain a subscription file?

    Needed because the search API is asked to sort by stars, which fills the
    top of the list with famous projects that have nothing to do with
    subscriptions. Measured: of the first five results, two were
    ChromeAppHeroes (a browser extension list) and TelegramGroup (a channel
    index). With a per-run probe budget, spending it on those means never
    reaching the repos that would have paid off -- a 40-repo run probed forty
    projects and found nothing, while the subscription repos sat further down
    the same list.

    Cheap signals only, all available from search results without extra
    requests: the words in the name and description, and whether the pushed
    date is recent.
    """
    name = (repo.get("full_name") or "").lower()
    desc = (repo.get("description") or "").lower()
    blob = name + " " + desc

    score = 0
    for word, weight in (("clash", 5), ("订阅", 5), ("subscribe", 4),
                         ("subscription", 4), ("sub", 2), ("v2ray", 3),
                         ("vmess", 3), ("shadowsocks", 3), ("proxy", 2),
                         ("节点", 4), ("节点分享", 5), ("免费", 3),
                         ("free", 2), ("机场", 4), ("trojan", 2)):
        if word in blob:
            score += weight
    # Things that are definitely not subscription repositories.
    for word in ("telegram", "awesome", "tutorial", "chrome extension",
                 "课程", "教程", "导航", "book", "learn", "interview"):
        if word in name:
            score -= 4
    # Popularity is a weak tiebreak, not a ranking signal on its own.
    score += min(repo.get("stars", 0) / 5000.0, 3)
    return score


def search_repos(fresh_days=45, per_query=30):
    since = (time.time() - fresh_days * 86400)
    found = {}
    for q in QUERIES:
        full = "%s pushed:>%s" % (q,
                                  time.strftime("%Y-%m-%d", time.localtime(since)))
        url = ("https://api.github.com/search/repositories?q=%s"
               "&sort=stars&order=desc&per_page=%d"
               % (urllib.parse.quote(full), per_query))
        d = _api(url)
        if not d or "items" not in d:
            continue
        new = 0
        for it in d["items"]:
            fn = it.get("full_name")
            if fn and fn not in found:
                found[fn] = {"full_name": fn,
                             "branch": it.get("default_branch") or "main",
                             "stars": it.get("stargazers_count", 0),
                             "pushed": it.get("pushed_at", ""),
                             "description": it.get("description") or ""}
                new += 1
        _log("%-46s +%d" % (q[:46], new))
        time.sleep(2.5)               # be polite to the API
    return found


def _exists(url):
    """
    Is there a file at this URL? Cheap enough to run on every candidate path.

    Worth it because the candidate list is long and most entries are wrong for
    any given repository. A 404 from GitHub's raw host comes back in
    milliseconds; a real 3 MB subscription takes seconds. Spending one quick
    request to avoid downloading several megabytes is a good trade when there
    are forty-odd paths to try.
    """
    try:
        r = subprocess.run(
            ["curl.exe", "-s", "-o", "NUL", "-L", "-m", "6",
             "-r", "0-0", "-w", "%{http_code}", url],
            capture_output=True, timeout=12, creationflags=0x08000000)
        return (r.stdout or b"").strip().startswith((b"2", b"3"))
    except Exception:
        return False


def probe(repo, known_urls, deadline=None, timeout=12):
    """
    Try the common paths for one repo and return the first that looks valid.

    Stops at the first hit -- one subscription per repository keeps the library
    small and there is nothing to gain from a repo's second file.

    `deadline` is an absolute time.time() value. It is checked before every
    request, because the whole point of it is that discovery has to finish: this
    runs when every subscription is dead and the program is trying to recover,
    and a slow search that hangs for an hour is worse than a shorter one. When
    the budget is gone we stop starting new requests instead of waiting for the
    ones in flight.
    """
    raw_url = "https://raw.githubusercontent.com/%s/%s/%%s" % (
        repo["full_name"], repo["branch"])
    for path in CANDIDATE_PATHS:
        if deadline and time.time() > deadline:
            # Not logged here: a dozen threads hitting the same wall would
            # print a dozen identical lines and bury the lines that matter.
            # run() reports the budget once, with a count.
            return []
        url = raw_url % path
        if url in known_urls:
            continue
        if not _exists(url):
            continue
        data = _curl(url, timeout)
        if data and len(data) > 3000 and _valid_subscription(data):
            return [(path, data)]
    return []


def _probe_worker(repo, known, deadline, timeout, out):
    """Thread body: probe one repo and drop its result in `out`."""
    try:
        hits = probe(repo, known, deadline, timeout)
    except Exception:
        hits = []
    if hits:
        out.append((repo, hits))


def _probe_worker_safe(repo, known, deadline, timeout, collect):
    """Thread body for run(): never raises, hands results back via collect."""
    try:
        hits = probe(repo, known, deadline, timeout)
    except Exception:
        hits = []
    if hits:
        collect(repo, hits)


def run(fresh_days=45, dry_run=False, max_repos=60, workers=12,
        budget_s=180, timeout=12):
    """
    Returns the number of sources admitted.

    Every candidate goes through the same overlap gate as everything else; that
    is what stops discovery from becoming the thing that ruins the library.

    The budget and the worker pool exist because of how this was measured. It
    used to probe repositories one at a time, serially, trying up to 19 paths
    with a 25 second timeout each: 120 repos x 19 paths x 25s is a theoretical
    worst case of about 15 hours, and a real run was still going after nine
    minutes. For the one situation discovery exists to handle -- every source
    dead, no internet, the program trying to rescue itself -- that is
    unusable. A slower, smaller search that reliably finishes is worth more
    here than an exhaustive one that never does.

    So: capped repo count, a hard wall-clock budget, a per-request timeout, and
    parallel probes. The default budget of three minutes is a compromise --
    long enough to look at a few dozen repositories, short enough that a user
    watching a tray icon does not assume the program has hung.
    """
    srcs = ms.load_sources()
    known = {s.get("url") for s in srcs if s.get("url")}
    _log("library has %d sources (%d enabled)"
         % (len(srcs), len([s for s in srcs if s.get("enabled")])))

    repos = search_repos(fresh_days=fresh_days)
    _log("search returned %d repositories" % len(repos))
    if not repos:
        return 0
    # Relevance first, popularity second. Sorting purely by stars -- which is
    # what the API's own sort parameter suggests -- spends the whole probe
    # budget on famous projects that do not publish subscriptions.
    ordered = sorted(repos.values(),
                     key=lambda r: (-_relevance(r), -r["stars"]))[:max_repos]
    _log("probing %d repositories, %d at a time, %ds budget"
         % (len(ordered), workers, budget_s))
    _log("top candidates: %s"
         % ", ".join("%s(%d)" % (r["full_name"].split("/")[1], _relevance(r))
                     for r in ordered[:5]))

    deadline = time.time() + budget_s
    found = []
    lock = threading.Lock()

    def collect(repo, hits):
        with lock:
            found.append((repo, hits))

    pool = []
    for repo in ordered:
        if time.time() > deadline:
            _log("time budget spent; leaving %d repositories unprobed"
                 % (len(ordered) - len(pool)))
            break
        t = threading.Thread(target=_probe_worker_safe,
                             args=(repo, known, deadline, timeout, collect),
                             daemon=True)
        t.start()
        pool.append(t)
        # A small stagger keeps a burst of simultaneous connections from
        # tripping rate limits, while still overlapping the slow ones.
        while len([p for p in pool if p.is_alive()]) >= workers:
            time.sleep(0.2)

    # The budget bounds starting new work; requests already in flight still have
    # to be waited for. Their own timeouts are what bound that, so join with a
    # cap and let the daemon threads die with the process if they overrun --
    # better a slightly incomplete search than a run that ignores its own limit.
    hard_stop = deadline + timeout + 10
    for t in pool:
        left = hard_stop - time.time()
        if left <= 0:
            break
        t.join(timeout=left)

    _log("%d of %d repositories had a usable subscription" % (len(found), len(pool)))

    admitted = 0
    for repo, hits in found:
        for path, data in hits:
            name = re.sub(r"[^A-Za-z0-9_]", "_",
                          repo["full_name"].split("/")[1] + "_"
                          + os.path.splitext(os.path.basename(path))[0])
            url = ("https://raw.githubusercontent.com/%s/%s/%s"
                   % (repo["full_name"], repo["branch"], path))
            P.ensure_dirs()
            tmp = os.path.join(P.SUBS, name + ".yaml")
            with open(tmp, "wb") as fh:
                fh.write(data)
            if dry_run:
                _log("  would admit %-32s %d nodes" % (name[:32], len(data)))
                continue
            try:
                ok, verdict, detail = ms.admit(name, tmp)
            except Exception as e:
                ok, verdict, detail = False, "error", str(e)[:60]
            if ok:
                admitted += 1
                _log("  ADMIT  %-30s %s" % (name[:30], detail))
            else:
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                _log("  skip   %-30s %s (%s)" % (name[:30], verdict, detail[:40]))

    _log("admitted %d new source(s) in %.0fs" % (admitted, budget_s))
    return admitted


if __name__ == "__main__":
    run(dry_run="--dry-run" in os.sys.argv)