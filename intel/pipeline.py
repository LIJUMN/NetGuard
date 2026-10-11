"""
pipeline.py — the whole screening pipeline, in pure Python.

Why this exists
---------------
The PowerShell original (pipeline/pipeline.ps1) shells out to `python` for
every step, which means the project cannot run on a machine that has no
Python installed. This module is the same logic with no external interpreter,
so a frozen NetGuard.exe is genuinely portable: double-click, done.

The PowerShell version is kept for people who want to read or hack on the
orchestration without a Python interpreter in the loop. Both share intel/.

Stages
------
  0  idle check      do not fight the user for bandwidth
  1  prune sources   drop re-posts, free for every future run
  2  refresh         pull each enabled subscription (multi-mirror fallback)
  3  rebuild config  regenerate config.yaml from the template
  4  reload+wait     hot-reload, wait until AUTO is populated
  5  latency screen  concurrent probe of every node          5789 -> ~57
  6  extract         full node definitions for the winners
  7  bandwidth       clash-speedtest, real download speed     40 -> ~20
  8  report          feed the reputation library
  9  verify          prove the wall is reachable through the proxy
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import paths as P  # noqa: E402
from intel import source_intel as si  # noqa: E402
from intel import manage_sources as ms  # noqa: E402

MIRRORS = ["", "https://gh-proxy.com/", "https://ghfast.top/", "https://ghproxy.net/"]
HEALTH_URL = "https://www.google.com/generate_204"


def health_url():
    """
    The endpoint used to decide whether a node really works.

    Not hardcoded on purpose: doctor verifies which one answers from this
    machine and caches it, because a dead health-check endpoint is worse than
    none -- it makes every node look broken.
    """
    try:
        from intel import doctor
        st = doctor._load()
        urls = st.get("health_urls") or []
        if urls and doctor._health_ok(urls[0]):
            return urls[0]
        url, _ = doctor.pick_health_url(urls)
        st["health_urls"] = [url]
        doctor._save(st)
        return url
    except Exception:
        return HEALTH_URL

# PowerShell hides its window with this; harmless for non-console binaries.
HIDE = 0x08000000


# ----------------------------------------------------------------- logging

_log_fh = None


def log(msg):
    global _log_fh
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if _log_fh is None:
            P.ensure_dirs()
            _log_fh = open(P.PIPELINE_LOG, "a", encoding="utf-8")
        _log_fh.write(line + "\n")
        _log_fh.flush()
    except Exception:
        pass
    print(line)


def set_state(**kw):
    cur = {}
    try:
        with open(P.TRAY_STATE, encoding="utf-8") as fh:
            cur = json.load(fh)
    except Exception:
        pass
    cur.update(kw)
    cur["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        P.ensure_dirs()
        tmp = P.TRAY_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False)
        os.replace(tmp, P.TRAY_STATE)
    except Exception:
        pass


# ----------------------------------------------------------------- helpers

def _run(cmd, timeout=300, capture=True):
    try:
        return subprocess.run(cmd, capture_output=capture, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=HIDE)
    except Exception as e:
        log("  command failed: %s (%s)" % (" ".join(cmd[:2]), e))
        return None


def _http_get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _curl_json(url, timeout=120):
    """
    Go through curl rather than urllib for anything that can be large or
    non-UTF8. Returns bytes; caller parses.
    """
    tmp = os.path.join(P.LOGS, "_api.json")
    try:
        P.ensure_dirs()
        r = _run(["curl.exe", "-s", "-o", tmp, "-m", str(timeout), url], timeout=timeout + 20)
        if r is None or not os.path.exists(tmp):
            return None
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _core_alive():
    try:
        _http_get(P.API + "/version", 3)
        return True
    except Exception:
        return False


def _wait_core(timeout=30):
    for _ in range(timeout * 2):
        if _core_alive():
            return True
        time.sleep(0.5)
    return False


def _select_group(name):
    body = json.dumps({"name": name}).encode("utf-8")
    req = urllib.request.Request(P.API + "/proxies/PROXY", data=body,
                                 method="PUT",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)


def _reload(cfg_path):
    """
    Hot-reload the config into the running core.

    The core refuses a path outside its own home directory ("path is not
    subpath of home directory or SAFE_PATHS"). That is a legitimate safety
    rail, and it means a reload can fail for a reason that has nothing to do
    with the config being wrong.

    It used to be swallowed, so the run carried on and reported success while
    the core was still serving the config it booted with -- the fast set was
    written to disk and never actually used. Now the reason is logged, and the
    caller decides whether to fall back to restarting the core.
    """
    body = json.dumps({"path": cfg_path.replace("\\", "/")}).encode("utf-8")
    req = urllib.request.Request(P.API + "/configs?force=true", data=body,
                                 method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
        return True, ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return False, "HTTP %s %s" % (e.code, detail)
    except Exception as e:
        return False, str(e)[:200]


def _core_home():
    """Where the running core thinks its home directory is.

    Read from the core itself rather than assumed. If our config path is not
    under it, a hot-reload can never work and we must restart the core instead
    of retrying forever.
    """
    try:
        with urllib.request.urlopen(P.API + "/configs", timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return (d or {}).get("home", "") or ""
    except Exception:
        return ""


def _restart_core(cfg):
    """
    Last resort when a hot-reload is impossible: bounce the core.

    The order matters and getting it wrong is not survivable:
      1. stop whatever is holding the port,
      2. THEN start the new one.

    An earlier version called subprocess.call() on the new core straight away.
    The old core still held 7890, the new one exited instantly, and because
    call() waits for the child the whole pipeline hung there for 23 minutes
    with no output. A restart that can hang is worse than no restart: the run
    never finishes and never reports.

    So the old process is killed by name first, the port is given a moment to
    clear, and the replacement is started detached so nothing here can block on
    it. Its exit status is not our business -- the check that matters is done
    afterwards by asking the API whether it came up.
    """
    # mihomo is started detached from the tray, so there is no handle to close.
    # Killing by image name is the only handle available, and it is also
    # exactly right: every core we started is a core we own.
    for image in ("mihomo.exe", "clash-speedtest.exe"):
        try:
            subprocess.call(["taskkill", "/F", "/IM", image],
                            capture_output=True, timeout=20)
        except Exception:
            pass

    for _ in range(10):
        time.sleep(1)
        if not _port_busy(7890):
            break

    try:
        subprocess.Popen([P.MIHOMO_EXE, "-d", P.MIHOMO_DIR, "-f", cfg],
                         creationflags=0x08000000 | 0x00000008,
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:
        log("  core restart failed to launch: %s" % e)
        return False

    # Confirm it actually came back rather than assuming the launch worked.
    for _ in range(15):
        time.sleep(2)
        try:
            urllib.request.urlopen(P.API + "/version", timeout=3)
            log("  core is back up")
            return True
        except Exception:
            continue
    log("  core did not come back within 30s")
    return False


def _port_busy(port):
    """Is something listening on this port right now?"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _apply_config(cfg):
    """
    Get a validated config actually in force.

    Tries a hot-reload first, and only restarts the core when the reload is
    refused. Returns True when the config is believed to be live.
    """
    ok, msg = _reload(cfg)
    if ok:
        return True
    home = _core_home()
    log("  reload refused: %s" % msg)
    if home and P.MIHOMO_DIR.lower() not in home.lower().replace("/", "\\"):
        log("  the running core uses a different home (%s); restarting it"
            % home)
    else:
        log("  restarting the core so the new config takes effect")
    return _restart_core(cfg)


def _test_config(exe, cfg):
    """mihomo has a -t flag. Use it instead of trusting our YAML."""
    if not os.path.exists(exe):
        return False, "core binary missing at %s" % exe
    r = _run([exe, "-d", P.MIHOMO_DIR, "-f", cfg, "-t"], timeout=60)
    out = ((r.stdout or "") + (r.stderr or "")) if r else ""
    return ("successful" in out), out.strip().splitlines()[-1:] and \
        out.strip().splitlines()[-1] or out.strip()


# ----------------------------------------------------------------- stages

def stage_idle(wait_rounds=18):
    """
    Sample the core's byte counters. If the link moved a lot in the window,
    the user is online and this run should stand down.
    """
    from intel import idle as _idle
    busy, moved = _idle.busy_once()
    if not busy:
        return True
    log("link busy (%.1f MB in 3s), waiting up to %d rounds" %
        (moved / 1048576.0, wait_rounds))
    for i in range(wait_rounds):
        time.sleep(10)
        busy, moved = _idle.busy_once()
        if not busy:
            log("link went idle after %d waits" % (i + 1))
            return True
    log("still busy after ~%ds -> postponing" % (wait_rounds * 10))
    return False


def stage_prune():
    try:
        kept, aliased, report = si.prune_redundant()
        for line in report:
            log("  prune: %s" % line)
        log("  active %d, aliased away %d" % (len(kept), len(aliased)))
        return kept
    except Exception as e:
        log("  prune skipped: %s" % e)
        return None


def stage_refresh(sources):
    """Pull every enabled subscription, trying mirrors in order."""
    for s in sources:
        if not s.get("enabled") or not s.get("url"):
            continue
        out = os.path.join(P.SUBS, s["name"] + ".yaml")
        custom = (s.get("added") == "custom" or s.get("priority"))
        # A hand-added subscription is allowed to be small. A rolling personal
        # subscription with a dozen nodes is perfectly good, and the 3 KB floor
        # that exists to reject 404 pages would throw it away.
        floor = 200 if custom else 3000
        got = False
        best = 0
        for m in MIRRORS:
            try:
                r = _run(["curl.exe", "-L", "-s", "-m", "45", "-o", out,
                          "-w", "%{http_code}", m + s["url"]], timeout=60)
                code = (r.stdout or "").strip() if r else ""
            except Exception:
                code = ""
            if os.path.exists(out):
                best = max(best, os.path.getsize(out))
            if os.path.exists(out) and os.path.getsize(out) > floor:
                got = True
                break
        if got:
            s["fails"] = 0
        else:
            s["fails"] = int(s.get("fails") or 0) + 1
            # Never auto-disable a source the user added by hand. A paid
            # subscription that times out for five runs -- an expired token, a
            # provider outage, a network hiccup -- would otherwise be switched
            # off permanently and silently, and the user would have no idea why
            # their paid nodes stopped being used.
            if s["fails"] >= 5 and not custom:
                s["enabled"] = False
                log("  source disabled after 5 failures: %s" % s["name"])
            elif s["fails"] >= 5 and custom:
                log("  %s has failed %d times but is yours -- keeping it enabled"
                    % (s["name"], s["fails"]))
            else:
                log("  fetch failed (%d): %s (%d bytes)"
                    % (s["fails"], s["name"], best))
    ms.save_sources([s for s in sources if s.get("added") != "custom"])
    active = [s for s in sources if s.get("enabled")]
    log("sources active = %d" % len(active))
    return active


def stage_config(active):
    """
    Regenerate config.yaml and prove the core accepts it.

    The template is written against today's mihomo. Today's mihomo will not
    exist in ten years, so when the core rejects a field we strip optional
    ones until it is happy again rather than leaving the user without a proxy.
    """
    from intel import doctor
    ok, level = doctor.make_config(active)
    if not ok:
        return None, level
    if level:
        log("  config needed %d degradation(s) to satisfy this core" % level)
    return P.CONFIG_YAML, level


def stage_reload_wait(cfg):
    """Put the config in force, then wait until the provider list is populated."""
    if not _apply_config(cfg):
        log("  could not get the new config into force")
        return False
    log("config in force; waiting for AUTO to populate")
    for i in range(30):
        time.sleep(5)
        try:
            d = json.loads(_http_get(P.API + "/proxies/AUTO", 20).decode("utf-8", "replace"))
            n = len(d.get("all") or [])
            if n > 20:
                log("AUTO ready: %d options after %ds" % (n, (i + 1) * 5))
                return True
        except Exception:
            pass
    log("WARN AUTO not ready after 150s, probing anyway")
    return False


def stage_latency():
    """Concurrent probe of every node through the core's own delay group."""
    url = (P.API + "/group/AUTO/delay?url=" +
           urllib.parse.quote(health_url(), safe="") + "&timeout=5000")
    t0 = time.time()
    raw = _curl_json(url, 150)
    if not raw:
        log("ABORT latency probe returned nothing")
        return []
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        log("ABORT latency parse failed: %s" % e)
        return []
    names = []
    for k, v in d.items():
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            try:
                names.append((int(v), k))
            except Exception:
                pass
    names.sort()
    log("stage-1 latency %.1fs  alive=%d" % (time.time() - t0, len(names)))
    return [n for _, n in names]


def stage_extract(names):
    """Re-read the source files and pull out the full definition of each winner."""
    from intel import extract_fast as _ef
    out = os.path.join(P.LOGS, "available.yaml")
    P.ensure_dirs()
    try:
        picked, missing = _ef.extract(names, out)
    except RuntimeError as e:
        log("stage-2 extract failed: %s" % e)
        return None, 0
    log("stage-2 extract: picked %d / wanted %d" % (picked, len(names)))
    return out, picked


def stage_bandwidth(avail_yaml, min_kb=60, top=40, bytes_per_test=1_000_000):
    """
    clash-speedtest is a Go binary with the core embedded. It is the only
    place we measure real download throughput, so this stage is the slow one
    and also the only trustworthy one.
    """
    if not os.path.exists(P.CST_EXE):
        log("WARN clash-speedtest not present at %s" % P.CST_EXE)
        return None, 0
    fast = os.path.join(P.LOGS, "fast.yaml")
    try:
        os.remove(fast)
    except Exception:
        pass
    # The tester downloads from a fixed URL by default, and that default
    # (a Chrome installer on dl.google.com) has stopped serving. Every probe
    # timed out and the whole candidate set was rejected -- the instrument was
    # broken, the nodes were fine. So the endpoint is chosen by probing, using
    # the same "verify, never assume" rule as the health check.
    from intel import doctor as _doc
    server = _doc.pick_speedtest_url()
    log("stage-3 bandwidth endpoint: %s" % server.split("?")[0])
    floor_mb = max(min_kb / 1024.0, 0.01)
    t0 = time.time()
    r = _run([P.CST_EXE, "-c", avail_yaml, "-concurrent", "12",
              "-max-latency", "6s", "-timeout", "4s",
              "-min-download-speed", str(floor_mb),
              "-server-url", server,
              "-download-size", str(bytes_per_test),
              "-output", fast], timeout=1800)
    log("stage-3 speedtest %.1fs (floor %.2f MB/s via %s)"
        % (time.time() - t0, floor_mb, server.split("?")[0]))
    if not os.path.exists(fast):
        # No output file at all is a different failure from "nothing was fast
        # enough", and it used to be reported as the latter -- which reads as
        # "every node on this machine is slow", a conclusion with no evidence.
        tail = ""
        if r:
            blob = ((r.stdout or "") + (r.stderr or "")).strip()
            tail = blob[-300:]
        log("ABORT the speed tester produced no output file (exit %s)"
            % (r.returncode if r else "?"))
        if tail:
            log("  tester said: %s" % tail)
        set_state(stage="error", msg="speed tester failed")
        return None, 0
    txt = open(fast, encoding="utf-8", errors="replace").read()
    passed = len(re.findall(r"(?m)^\s*-\s+name", txt))

    # An empty result is only meaningful if the instrument worked. When every
    # download failed the tester still writes a well-formed file containing an
    # empty proxy list, so "0 passed" arrives looking exactly like "nothing was
    # fast enough". Those are opposite conclusions and the user acts differently
    # on each, so they must not look the same in the log.
    if passed == 0:
        spoke = ("download request" in txt or "deadline exceeded" in txt
                 or "no such host" in txt)
        if spoke:
            log("ABORT the bandwidth test could not download from %s -- the "
                "result says nothing about the nodes"
                % server.split("?")[0])
            set_state(stage="error", msg="bandwidth endpoint unreachable")
            return None, 0
        log("  every candidate was measured and none cleared %.2f MB/s"
            % floor_mb)
    # clash-speedtest emits a whole config including proxy-providers, which
    # mihomo's provider loader refuses. Strip it down to proxies only.
    proxies = []
    try:
        import yaml
        doc = yaml.safe_load(txt) or {}
        proxies = doc.get("proxies") or []
    except Exception as e:
        log("  parse failed: %s" % e)

    # ---- reachability gate -------------------------------------------
    # Speed is not reachability. Measured on a real run: of 21 nodes that
    # passed the bandwidth test, only 3 could complete a request to
    # google.com/generate_204. The rest looked fine and then timed out on
    # the actual TLS handshake. Writing those into the fast set means the
    # user picks a "fast" node and gets nothing.
    #
    # The core has to load the set before it can probe it, so this needs a
    # reload. If the probe cannot be run we keep the unverified set rather
    # than throw away nodes that may be fine.
    keep = list(proxies)
    if proxies:
        _write_fast(keep)
        # The core probes what it has loaded, so it needs the new set first.
        if _reload_quiet():
            time.sleep(6)
            alive = _probe_reachability()
        else:
            log("  reachability skipped: reload failed, keeping the raw set")
            alive = None
        if alive:
            by_name = {p.get("name"): p for p in proxies}
            keep = [by_name[n] for n in alive if n in by_name]
            _write_fast(keep)
            log("  reachability: kept %d of %d speed-passing nodes"
                % (len(keep), len(proxies)))
        elif alive is not None:
            log("  reachability: none answered, keeping the unverified set")

    passed = len(keep)
    log("stage-3 passed = %d" % passed)
    return fast, passed


def _reload_quiet():
    """Hot-reload and swallow the error. Returns True when it took."""
    try:
        body = json.dumps({"path": P.CONFIG_YAML.replace("\\", "/")}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(
            P.API + "/configs?force=true", data=body, method="PUT",
            headers={"Content-Type": "application/json"}), timeout=20)
        return True
    except Exception:
        return False


def _write_fast(proxies):
    """Write nodes_fast.yaml. mihomo's provider rejects anything but proxies."""
    import yaml
    P.ensure_dirs()
    with open(os.path.join(P.SUBS, "nodes_fast.yaml"), "w", encoding="utf-8") as fh:
        fh.write("proxies:\n")
        yaml.safe_dump(proxies, fh, allow_unicode=True,
                       default_flow_style=False, sort_keys=False)


def _probe_reachability(timeout=8000):
    """
    Ask the core to test every node in its FAST group at once.
    Returns the names that answered (fastest first), [] when none did, or
    None when the probe itself could not run.
    """
    url = (P.API + "/group/FAST/delay?url=" +
           urllib.parse.quote(health_url(), safe="") + "&timeout=%d" % timeout)
    t0 = time.time()
    raw = _curl_json(url, timeout=timeout + 60)
    if not raw:
        return None
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    pairs = []
    for k, v in d.items():
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            try:
                pairs.append((int(v), k))
            except Exception:
                pass
    pairs.sort()
    log("  reachability probe: %d of %d answered in %.1fs"
        % (len(pairs), len(d), time.time() - t0))
    return [n for _, n in pairs]


def stage_reachability(timeout=8000):
    """
    Which nodes can actually complete a request to the far side?

    This stage exists because low latency is not reachability. Measured on a
    real run: of 21 nodes that had passed both the latency screen and the
    bandwidth test, only 3 could complete google.com/generate_204. The rest
    answered the delay probe and then timed out on the actual request.

    We ask the core to test its own FAST group concurrently rather than
    looping here. Two reasons: it is 21 probes at once instead of one at a
    time, and it does not touch which node the user's traffic currently uses
    -- an earlier version promoted each node in turn and visibly disrupted
    the live connection every time.

    Returns the names that answered, fastest first, or None when the probe
    itself failed (which is not the same as "none of them work").
    """
    url = (P.API + "/group/FAST/delay?url=" +
           urllib.parse.quote(health_url(), safe="") +
           "&timeout=%d" % timeout)
    t0 = time.time()
    raw = _curl_json(url, timeout=timeout + 60)
    if not raw:
        log("WARN reachability probe returned nothing; keeping the previous set")
        return None
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        log("WARN reachability parse failed: %s" % e)
        return None
    pairs = []
    for k, v in d.items():
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            try:
                pairs.append((int(v), k))
            except Exception:
                pass
    pairs.sort()
    alive = [n for _, n in pairs]
    log("stage-2b reachability: %d of %d FAST candidates actually connect (%.1fs)"
        % (len(alive), len(d), time.time() - t0))
    return alive


def stage_reputation(avail_yaml, fast_yaml):
    from intel import speed_result as _sr

    tested = _sr.load(avail_yaml)
    passed = _sr.load(fast_yaml) if fast_yaml and os.path.exists(fast_yaml) else []
    if not tested:
        return
    pmap = {}
    for n in passed:
        fp = si.fingerprint(n)
        if fp:
            pmap[fp] = n
    results = {}
    for n in tested:
        fp = si.fingerprint(n)
        if not fp:
            continue
        hit = pmap.get(fp)
        if hit:
            sp = _sr.measure_from_name(hit.get("name") or "")
            if not sp:
                sp = int(n.get("speed") or n.get("download-speed") or 0)
            results[fp] = {"alive": True, "speed": sp, "name": hit.get("name") or n.get("name")}
        else:
            results[fp] = {"alive": False, "speed": 0, "name": n.get("name")}
    total, black = si.rep_record_batch(results)
    ok = sum(1 for r in results.values() if r["alive"])
    log("  reputation: %d tested, %d passed, %d newly blacklisted (%d tracked)"
        % (len(results), ok, black, total))


def stage_verify():
    ok = 0
    for u in ("https://www.google.com/generate_204", "https://github.com"):
        r = _run(["curl.exe", "-s", "-x", "http://" + P.PROXY_ADDR, "-o", "NUL",
                  "-m", "20", "-w", "%{http_code}", u], timeout=30)
        code = (r.stdout or "").strip() if r else "000"
        if code != "000":
            ok += 1
    return ok


# ----------------------------------------------------------------- driver

def run(min_speed_kb=60, top_speed=40, bytes_per_test=1_000_000,
        skip_sources=False, skip_idle=False, log_sleep=0):
    t0 = time.time()
    P.ensure_dirs()

    lock = _acquire_lock()
    if not lock:
        log("pipeline already running -> exit")
        return 0

    try:
        log("=== pipeline start ===")
        set_state(stage="harvesting", alive=0, fast=0)

        # ---- 0. never fight the user for bandwidth
        if not skip_idle:
            set_state(stage="waiting", msg="link busy, waiting for idle")
            if not stage_idle():
                log("postponed: link stayed busy")
                set_state(stage="done", msg="postponed (link busy)", elapsed=0)
                return 0
            log("link idle -> proceeding")

        # ---- 1. snapshot before anything can be changed
        try:
            from intel import doctor
            doctor.backup("before-run")
        except Exception as e:
            log("  snapshot skipped: %s" % e)

        # ---- 2. prune first: every future run gets cheaper for free
        stage_prune()

        # ---- 3. refresh
        sources = ms.load_sources()
        if skip_sources:
            log("sources: skipped (-SkipSources)")
        else:
            sources = stage_refresh(sources)
        active = [s for s in sources if s.get("enabled") and s.get("url")]
        if not active:
            # Every source gone is the normal end state of free repos, not an
            # emergency. Go find new ones instead of giving up. This is what
            # keeps the thing alive with nobody maintaining it.
            log("no usable source left -- asking discovery for new ones")
            try:
                from intel import doctor
                doctor.source_health()
                from intel import manage_sources as _ms
                sources = _ms.load_sources()
                active = [s for s in sources
                          if s.get("enabled") and s.get("url")]
                log("discovery left %d usable source(s)" % len(active))
            except Exception as e:
                log("discovery failed: %s" % e)
        if not active:
            log("ABORT still no enabled sources")
            set_state(stage="error", msg="no enabled sources")
            return 1

        # ---- 3. config, degraded until this core accepts it
        cfg, level = stage_config(active)
        if not cfg:
            log("ABORT no config the core will accept")
            set_state(stage="error", msg="config invalid for this core")
            return 1
        log("config valid (degrade level %d)" % level)

        # ---- 4. reload, then wait until providers are actually there
        stage_reload_wait(cfg)

        # ---- 5. latency screen
        names = stage_latency()
        if not names:
            log("ABORT no live node")
            set_state(stage="error", msg="no live node")
            return 1

        # credit each source before names get rewritten by the speed test
        try:
            from intel import record_yield as _ry
            for line in _ry.record(set(names), time.strftime("%H:%M")):
                log("  yield: %s" % line)
        except Exception as e:
            log("  yield skipped: %s" % e)

        cand = names[:top_speed]
        log("stage-2 testing top %d of %d by latency" % (len(cand), len(names)))

        # ---- 6. extract definitions
        avail, picked = stage_extract(cand)

        # ---- 7. real bandwidth
        fast, passed = stage_bandwidth(avail, min_speed_kb, bytes_per_test=bytes_per_test)

        # ---- 8. learn
        if passed > 0:
            stage_reputation(avail, fast)
        else:
            log("reputation: skipped (nothing passed the speed filter)")

        # ---- 9. reload with the fast set and prove it works
        ok2, msg2 = _test_config(P.MIHOMO_EXE, cfg)
        if ok2:
            if _apply_config(cfg):
                time.sleep(6)
                log("fast set is now in force")
            else:
                log("fast set written but NOT loaded -- the core kept its old config")
        else:
            log("reload skipped: %s" % msg2)

        verified = stage_verify()

        # ---- 10. reports. Written on every finished run, successful or not:
        # a run that quietly achieved nothing is exactly the run a human needs
        # to be able to read about afterwards.
        try:
            from intel import report as _rp
            made = _rp.write_all()
            log("reports written: %s" % ", ".join(os.path.basename(m)
                                                   for m in made))
        except Exception as e:
            log("report generation failed: %s" % e)

        elapsed = time.time() - t0
        log("=== pipeline done: alive=%d passed=%d verify=%d/2 in %.0fs ==="
            % (len(names), passed, verified, elapsed))
        set_state(stage="done", alive=len(names), fast=passed,
                  verify=verified, elapsed=int(elapsed), msg="done")
        if log_sleep:
            time.sleep(log_sleep)
        return 0
    finally:
        _release_lock(lock)


def _lock_holder_alive():
    """
    Is the process that took this lock still running?

    The 30-minute staleness rule was the only protection, and it is a bad one:
    a run killed at minute one leaves a lock that blocks every run for the next
    half hour, and the user gets no diagnostics -- the pipeline just prints
    "already running" and exits 0, which looks like success.

    Writing our PID into the lock turns "is this stale?" into a question with a
    real answer. Age still matters as a backstop, because a PID can be recycled
    and a lock could outlive a crash without a finally block.
    """
    try:
        with open(P.PIPELINE_LOCK, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except Exception:
        return False, 0.0
    age = time.time() - os.path.getmtime(P.PIPELINE_LOCK)
    pid = 0
    # format: "<iso timestamp> pid=<n>"
    if "pid=" in raw:
        try:
            pid = int(raw.rsplit("pid=", 1)[1].strip())
        except ValueError:
            pid = 0
    if pid and pid != os.getpid():
        if _pid_alive(pid):
            return True, age
        return False, age
    # no usable pid: fall back to age alone
    return age < 1800, age


def _pid_alive(pid):
    """
    Is this process id running?

    Read as bytes on purpose: tasklist writes its output in the console code
    page (GBK on a Chinese Windows), and asking for text with a utf-8 codec
    raises UnicodeDecodeError deep inside the reader thread -- which surfaces as
    a mangled traceback and, worse, an empty result that makes every live
    process look dead. Byte comparison cannot fail that way.
    """
    if pid <= 0:
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                           capture_output=True, timeout=15)
        if r.returncode != 0:
            return False
        return str(pid).encode() in (r.stdout or b"")
    except Exception:
        return False


def _acquire_lock():
    """
    Atomic: O_CREAT|O_EXCL fails if the file already exists, so two processes
    cannot both win. A lock whose owner is gone is reclaimed immediately rather
    than after a fixed timeout.
    """
    P.ensure_dirs()
    try:
        if os.path.exists(P.PIPELINE_LOCK):
            alive, age = _lock_holder_alive()
            if alive:
                return None
            log("clearing a lock left behind by a process that is gone "
                "(%.0f min old)" % (age / 60.0))
            try:
                os.remove(P.PIPELINE_LOCK)
            except Exception:
                return None
        fd = os.open(P.PIPELINE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, ("%s pid=%d"
                      % (time.strftime("%Y-%m-%dT%H:%M:%S"), os.getpid())).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return None
    except Exception:
        return True          # never block the pipeline on a lock glitch


def _release_lock(ok):
    try:
        os.remove(P.PIPELINE_LOCK)
    except Exception:
        pass


# ----------------------------------------------------------------- cli

def main(argv):
    min_kb = int(os.environ.get("NETGUARD_MIN_KB", "60"))
    top = int(os.environ.get("NETGUARD_TOP", "40"))
    skip_sources = "--skip-sources" in argv or "-SkipSources" in argv
    skip_idle = ("--skip-idle" in argv or "-SkipIdleCheck" in argv
                 or os.environ.get("NETGUARD_SKIP_IDLE") == "1")
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    return run(min_speed_kb=min_kb, top_speed=top,
               skip_sources=skip_sources, skip_idle=skip_idle)


if __name__ == "__main__":
    import urllib.parse  # noqa: F401  (used in stage_latency)
    sys.exit(main(sys.argv[1:]))