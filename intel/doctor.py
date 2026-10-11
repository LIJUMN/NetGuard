"""
doctor.py — make the thing survive things we cannot predict.

The goal is a binary that still works in ten years without a maintainer. That
means every external assumption has to have a fallback, because each one WILL
break eventually:

  * the core binary goes stale (mihomo ships new versions constantly)
  * a config field gets deprecated and the core refuses to start
  * every subscription source disappears at once (free repos get deleted)
  * the health-check endpoint changes or starts blocking
  * GitHub rate-limits us or moves an endpoint

The pattern is always the same: detect, degrade one level, keep working.
Never "fix or die". A slower proxy beats no proxy.
"""
import os
import re
import sys
import json
import time
import shutil
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import paths as P  # noqa: E402

STATE = os.path.join(P.CONFIG, "doctor.json")
BACKUP_DIR = os.path.join(P.HOME, "backups")

# How many config generations to keep. Ten years of daily snapshots would be
# pointless; a handful of known-good ones is what actually matters.
KEEP_BACKUPS = 5


def _log(msg):
    print("[doctor] %s" % msg, flush=True)
    try:
        P.ensure_dirs()
        with open(os.path.join(P.LOGS, "doctor.log"), "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"degrade_level": 0, "health_urls": [], "core_version": ""}


def _save(d):
    try:
        P.ensure_dirs()
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception:
        pass


# ---------------------------------------------------------------- health urls

# Checked in order. The first two are used for reachability because they are
# widely mirrored; the rest are fallbacks for when those get blocked.
DEFAULT_HEALTH_URLS = [
    "https://www.google.com/generate_204",
    "https://www.gstatic.com/generate_204",
    "http://cp.cloudflare.com/generate_204",
    "https://connectivitycheck.gstatic.com/generate_204",
    "https://www.gstatic.com/ipranges/goog.json",
]

# Where the bandwidth test pulls bytes from.
#
# clash-speedtest ships with a hardcoded default of a Chrome installer on
# dl.google.com. That URL stopped serving in a way this program can see: every
# probe came back "context deadline exceeded", so all 15 candidates were
# rejected and the fast set ended up empty -- a broken measuring instrument
# reporting "every node is slow". The fix is not a better default, it is the
# same lesson as the health URL: never trust a fixed endpoint, verify it.
#
# Ordered by how likely they are to answer from a mainland connection once the
# proxy is up. The Google entry is last on purpose: it is what clash-speedtest
# uses by default and it is the one that broke.
SPEEDTEST_URLS = [
    "http://speedtest.tele2.net/10MB.zip",
    "https://speed.cloudflare.com/__down?bytes=2000000",
    "https://proof.ovh.net/files/10Mb.dat",
    "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
]

# Endpoints that have already proved dead are not retried for a while. Without
# this the list is walked from the top on every single run, so the slow failures
# come first and a working endpoint further down never gets reached in time.
_SPEEDTEST_DEAD_UNTIL = {}


def pick_speedtest_url():
    """
    An endpoint that actually serves bytes right now.

    Measured through the proxy when there is one, because that is the path the
    bytes will really travel. Two details that matter:

      * accept any 2xx or 3xx. Asking for a byte range makes many servers answer
        403 or 416 instead of 200, and treating that as "dead" rejects endpoints
        that download perfectly well with a normal GET -- which is exactly what
        the tester does.
      * remember the dead ones. Otherwise the same slow failures are paid on
        every run, and the fallback ends up picking the first entry, which is
        the one most likely to be the thing that broke.

    Returns "" if nothing answered. An empty result is honest; a wrong one is
    worse, because it makes the bandwidth stage reject every node for a reason
    that has nothing to do with the nodes.
    """
    use_proxy = _core_up()
    now = time.time()
    alive = []
    for url in SPEEDTEST_URLS:
        if _SPEEDTEST_DEAD_UNTIL.get(url, 0) > now:
            continue
        # -o NUL is what keeps the response body out of stdout. Without it the
        # body and the -w status code end up in the same string, so a healthy
        # 206 arrives as "400000000000..." and every endpoint looks dead --
        # while a few megabytes of junk get appended to the doctor log each
        # time. The flag has to come before the URL.
        cmd = ["curl.exe", "-s", "-o", "NUL", "-m", "9",
               "-w", "%{http_code}"]
        if use_proxy:
            cmd[4:4] = ["-x", "http://" + P.PROXY_ADDR]
        cmd.append(url)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=15, creationflags=0x08000000)
            code = (r.stdout or "").strip()
        except Exception:
            code = ""
        # Only ever look at the status code. Anything else here is response
        # body that leaked past -o NUL, and a log line should never be allowed
        # to grow by megabytes.
        if len(code) > 3:
            code = code[:3]
        if code.startswith(("2", "3")):
            _SPEEDTEST_DEAD_UNTIL.pop(url, None)
            _log("bandwidth-test endpoint ok: %s (%s)"
                 % (url.split("?")[0], code))
            return url
        _SPEEDTEST_DEAD_UNTIL[url] = now + 3600
        _log("bandwidth-test endpoint dead: %s (%s)"
             % (url.split("?")[0], code or "no answer"))

    # Nothing answered. If there is a proxy, it is almost certainly the reason:
    # the endpoint list is fine, the route is not. Say so instead of quietly
    # picking one and blaming the nodes.
    if alive:
        return alive[0]
    _log("no bandwidth-test endpoint answered%s"
         % (" (the proxy is up but not routing)" if use_proxy else ""))
    return SPEEDTEST_URLS[0]


def _probe_once(url, timeout=6):
    """
    Does this endpoint answer?

    Kept to a few seconds on purpose: probing five candidates at twelve
    seconds each means a minute of staring at a tray icon on a machine where
    they have all been blocked. A health check that is slow to fail is not a
    health check.
    """
    try:
        r = subprocess.run(
            ["curl.exe", "-s", "-o", "NUL", "-m", str(timeout),
             "-w", "%{http_code}", url],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout + 5,
            creationflags=0x08000000)
        code = (r.stdout or "").strip()
        return code if len(code) == 3 and code != "000" else ""
    except Exception:
        return ""


def _health_ok(url, timeout=8):
    return bool(_probe_once(url, timeout))


def pick_health_url(existing=None):
    """
    Find an endpoint that answers from inside this machine, right now.

    A dead health-check endpoint is worse than none: it makes every node look
    broken. So we verify rather than assume, cache the winner, and re-verify
    when it starts failing. The previously good one is always tried first so
    the common case costs one probe.
    """
    cands = list(existing or []) + DEFAULT_HEALTH_URLS
    seen, ordered = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    for url in ordered:
        code = _probe_once(url)
        if code:
            _log("health endpoint ok: %s (%s)" % (url, code))
            return url, code
    _log("no health endpoint answered; falling back to the first")
    return DEFAULT_HEALTH_URLS[0], "000"


# ---------------------------------------------------------------- config degrade
#
# What actually breaks a mihomo config over the years, measured rather than
# guessed:
#
#   * unknown keys        -> the core IGNORES them. Verified by injecting
#                            fake fields: still accepted. So stripping
#                            speculative keys is pointless.
#   * a rule pointing at a group that does not exist -> hard error.
#   * a missing proxy-groups block referenced by rules -> hard error.
#   * a removed/renamed field the core now requires -> hard error.
#
# So the degradation ladder removes whole FEATURES in order of how much we
# care, and stops at "a plain forward proxy with no split routing" -- which
# is useless for this project's purpose but still better than a core that
# refuses to start and takes the user's traffic down with it.

# Ordered worst-loss first. Each entry removes one named FEATURE and, crucially,
# everything that references it -- a config that drops a group but leaves a rule
# pointing at it is exactly the "hard error" case we are trying to survive.
#
# Each entry is (label, function). The function takes the config text and
# returns the text with that feature (and its dependants) removed.
#
# Why line-based functions instead of one clever regex: the previous version
# used regexes, and two of the five silently did nothing while still consuming a
# degradation level. The "split routing" pattern expected `rules:` before
# `proxy-groups:`, but in the template proxy-groups comes first, so the pattern
# matched zero characters -- the ladder reported "dropping split routing" and
# the config was unchanged. Meanwhile the `fast` pattern had nested quantifiers
# under (?s), which made `.` match newlines and turned the match into
# catastrophic backtracking that hung the whole process with no output.
# A structural edit cannot fail that way: if a block is not found, nothing is
# removed, and we say so out loud.
def _blocks(text):
    """
    Split a YAML document into (key, start_line, end_line) for every top-level
    block, plus a list of the proxy-group dicts.

    Deliberately a tiny hand-rolled reader rather than a YAML dependency: this
    runs inside a frozen single-file EXE, and the only thing we ever need is
    "where does this top-level key start and stop", which is answerable from
    indentation alone.
    """
    lines = text.splitlines()
    starts = []
    for i, ln in enumerate(lines):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):", ln)
        if m:
            starts.append((i, m.group(1)))
    out = []
    for n, (i, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((key, i, end))

    groups = []
    in_groups = False
    for key, i, end in out:
        if key == "proxy-groups":
            in_groups = True
            j = i + 1
            while j < end:
                m = re.match(r"^\s*-\s*name:\s*(\S+)", lines[j])
                if m:
                    k = j + 1
                    while k < end and not re.match(r"^\s*-\s*name:", lines[k]):
                        k += 1
                    groups.append((m.group(1), j, k))
                    j = k
                else:
                    j += 1
            break
        if key != "proxy-groups":
            in_groups = False
    return lines, out, groups


def _render(lines):
    return "\n".join(lines) + "\n"


def _find_block(blocks, key):
    for key_, i, end in blocks:
        if key_ == key:
            return i, end
    return None


def _find_group(groups, name):
    for name_, i, end in groups:
        if name_ == name:
            return i, end
    return None


def _referenced(text_lines, group_names):
    """Is any of these group names referenced anywhere else?"""
    for ln in text_lines:
        s = ln.strip()
        for g in group_names:
            # a rule or a selector entry like "- WEB" / "- name: WEB" / "WEB,"
            if s in ("- %s" % g, "- %s," % g, "%s," % g):
                return True
            if s.startswith("- name: %s" % g):
                return True
    return False


# ---- individual degradation steps

def _drop_split_routing(text):
    """Remove the routing rules, leaving a config that proxies nothing.

    Last resort only: a core with no rules still starts and still listens, so
    the user keeps a working process and a manual switch, which beats a core
    that refuses to boot.
    """
    lines, blocks, _ = _blocks(text)
    span = _find_block(blocks, "rules")
    if not span:
        return text, False
    i, end = span
    # mode: rule with no rules would be rejected, so switch to global and pin
    # the selector directly.
    out = []
    for n, ln in enumerate(lines):
        if n == i:
            break
        if re.match(r"^mode:", ln):
            out.append("mode: global")
            continue
        out.append(ln)
    return _render(out), True


def _drop_purpose_groups(text):
    """Remove the WEB / DOWN / MEDIA groups and every reference to them."""
    lines, blocks, groups = _blocks(text)
    victims = [g for g in ("WEB", "DOWN", "MEDIA") if _find_group(groups, g)]
    if not victims:
        return text, False
    kill = set()
    for g in victims:
        span = _find_group(groups, g)
        if span:
            kill.update(range(span[0], span[1]))
    # references: "- WEB" inside PROXY's proxies list, and rules "- ...,WEB"
    for n, ln in enumerate(lines):
        if n in kill:
            continue
        s = ln.strip()
        for g in victims:
            if s in ("- %s" % g, "- %s," % g):
                kill.add(n)
    return _render([ln for n, ln in enumerate(lines) if n not in kill]), True


def _drop_fast_set(text):
    """
    Remove the pre-screened fast provider and everything fed by it.

    The FAST/WEB/DOWN/MEDIA groups all say `use: [fast]`, and PROXY lists them.
    Dropping the provider while those remain is precisely the dangling-
    reference error the ladder exists to avoid, so this step removes the
    provider, the groups that consume it, and their references together.
    """
    lines, blocks, groups = _blocks(text)
    providers = [g for g in ("FAST", "WEB", "DOWN", "MEDIA") if _find_group(groups, g)]
    prov_span = None
    if blocks:
        for n, (key, i, end) in enumerate(blocks):
            if key == "proxy-providers":
                # the fast provider is the first child of proxy-providers
                j = i + 1
                while j < end:
                    m = re.match(r"^  ([A-Za-z0-9_-]+):", lines[j])
                    if m:
                        prov_span = (m.group(1), j)
                        break
                    if re.match(r"^\S", lines[j]):
                        break
                    j += 1
                break
    if not providers and not prov_span:
        return text, False

    kill = set()
    for g in providers:
        span = _find_group(groups, g)
        if span:
            kill.update(range(span[0], span[1]))
    if prov_span:
        name, j = prov_span
        # its block runs to the next sibling key at the same indent
        k = j + 1
        while k < len(lines):
            ln = lines[k]
            if ln.strip() and not re.match(r"^\s{4,}", ln):
                break
            if re.match(r"^  [A-Za-z0-9_-]+:", ln):
                break
            k += 1
        kill.update(range(j, k))
        # and any `use:` list that names it
        for n, ln in enumerate(lines):
            if ln.strip() == "- %s" % name:
                kill.add(n)
    for n, ln in enumerate(lines):
        if n in kill:
            continue
        s = ln.strip()
        for g in providers:
            if s in ("- %s" % g, "- %s," % g):
                kill.add(n)
    return _render([ln for n, ln in enumerate(lines) if n not in kill]), True


def _drop_profile(text):
    lines, blocks, _ = _blocks(text)
    span = _find_block(blocks, "profile")
    if not span:
        return text, False
    i, end = span
    return _render(lines[:i] + lines[end:]), True


def _drop_dns(text):
    lines, blocks, _ = _blocks(text)
    span = _find_block(blocks, "dns")
    if not span:
        return text, False
    i, end = span
    return _render(lines[:i] + lines[end:]), True


DEGRADE_LADDER = [
    ("split routing (domestic traffic now goes through the proxy)", _drop_split_routing),
    ("the purpose groups WEB/DOWN/MEDIA", _drop_purpose_groups),
    ("the pre-screened fast node set", _drop_fast_set),
    ("the profile block", _drop_profile),
    ("dns tuning", _drop_dns),
]


def _strip(text, step):
    """Apply one degradation step. Returns (text, actually_changed)."""
    label, fn = step
    try:
        new, changed = fn(text)
    except Exception as e:
        _log("degrade step '%s' failed: %s" % (label, e))
        return text, False
    if changed:
        _log("removed %s" % label)
    else:
        # Being honest here matters: a step that finds nothing must not consume
        # a degradation level, or the ladder burns its budget on no-ops and
        # gives up while the config is still perfectly fine.
        _log("nothing to remove for %s" % label)
    return new, changed


def make_config(active_sources):
    """
    Build config.yaml, verify it with the core's own -t flag, and degrade
    features until the core accepts it.

    This is the single most important function for longevity: the template is
    written against today's mihomo, and today's mihomo will not exist in ten
    years. Degrading a feature is always better than a core that will not
    start.

    The search always starts from the FULL template, never from the last known
    degraded level. That is deliberate and it is what makes the thing heal:
    resuming at the stored level meant a machine that degraded once stayed
    degraded forever, because the working configuration was never retried and
    the missing features could never come back. Starting from the top costs one
    extra core invocation on the rare runs where the config is genuinely broken,
    and it means a config problem that gets fixed -- a renamed field, a new core
    that accepts the old format again, a bad source removed from the library --
    repairs itself with no human involved.

    So: features are lost only while they are actually breaking something, and
    restored as soon as they stop.
    """
    prov, use = [], []
    for s in active_sources:
        prov.append('  %s:\n    type: file\n    path: "./subs/%s.yaml"\n'
                    '    health-check:\n      enable: true\n'
                    '      url: %s\n      interval: 300'
                    % (s["name"], s["name"], DEFAULT_HEALTH_URLS[0]))
        use.append("      - %s" % s["name"])

    tpl = open(P.TEMPLATE_YAML, encoding="utf-8").read()
    full = tpl.replace("{{PROVIDERS}}", "\n".join(prov)) \
              .replace("{{PROVIDER_USE}}", "\n".join(use))

    st = _load()
    was_degraded = int(st.get("degrade_level") or 0)

    # Level 0 is always tried first. That single test is what allows a machine
    # stuck in a degraded state to climb back out on its own.
    text = full
    level = 0
    while True:
        P.ensure_dirs()
        with open(P.CONFIG_YAML, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        ok, msg = _test_core()
        if ok:
            if level != was_degraded:
                if was_degraded and level == 0:
                    _log("config is healthy again -- all features restored")
                st["degrade_level"] = 0
                st.pop("last_degrade", None)
                _save(st)
            return True, level
        if level >= len(DEGRADE_LADDER):
            _log("config still rejected after dropping every optional feature")
            _log("core says: %s" % msg)
            # Nothing worked. Keep the last degrade level we actually reached so
            # the next run has a record, but do not pretend it is healthy.
            st["degrade_level"] = level
            _save(st)
            return False, level
        text, changed = _strip(text, DEGRADE_LADDER[level])
        if not changed:
            # nothing left to remove here; try the next step without recording a
            # degrade state we did not actually achieve
            level += 1
            continue
        level += 1
        st["degrade_level"] = level
        st["last_degrade"] = {"feature": DEGRADE_LADDER[level - 1][0],
                              "at": time.time()}
        _save(st)


def _test_core():
    """
    Ask the core to validate a config with its own -t flag.

    The directory has to exist before the core runs: it will not create it,
    and a missing config directory is reported as a generic failure that looks
    like "invalid config" -- which sent the degradation ladder stripping
    perfectly good features for nothing.
    """
    if not os.path.exists(P.MIHOMO_EXE):
        return False, "core binary missing"
    P.ensure_dirs()
    if not os.path.exists(os.path.dirname(P.CONFIG_YAML) or ""):
        os.makedirs(os.path.dirname(P.CONFIG_YAML), exist_ok=True)
    try:
        r = subprocess.run([P.MIHOMO_EXE, "-d", P.MIHOMO_DIR, "-f", P.CONFIG_YAML, "-t"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=90,
                           creationflags=0x08000000)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if "successful" in out.lower():
            return True, (out.splitlines()[-1] if out else "ok")
        # a filesystem complaint is not a config problem, and treating it as
        # one is what makes the ladder discard features for no reason
        if "can't create file" in out or "cannot find the path" in out.lower():
            return False, "environment: a required directory is missing"
        detail = ""
        for line in reversed(out.splitlines()):
            if "error" in line.lower() or "not found" in line.lower():
                detail = line[-160:]
                break
        return False, detail or (out.splitlines()[-1][-160:] if out else "unknown")
    except Exception as e:
        return False, str(e)[:120]


# ---------------------------------------------------------------- core upkeep

def _installed_version():
    """Read the version of the core we currently have, or '' if absent."""
    if not os.path.exists(P.MIHOMO_EXE):
        return ""
    try:
        r = subprocess.run([P.MIHOMO_EXE, "-v"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25,
                           creationflags=0x08000000)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r"(v[\d.]+)", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _core_up():
    try:
        with urllib.request.urlopen(P.API + "/version", timeout=2):
            return True
    except Exception:
        return False


def _latest_version():
    """
    The newest published core tag.

    The API is the obvious way and it is the wrong one: api.github.com is
    unreachable from a mainland connection, and the local proxy that would
    reach it may itself not be running yet -- which is exactly the situation
    where knowing about a new core matters most.

    So we also ask GitHub for the redirect on /releases/latest. That endpoint
    is on github.com itself, which the mirrors proxy, and it answers with a
    Location header containing the tag. No API, no token, no rate limit.
    """
    d = _api_json("https://api.github.com/repos/MetaCubeX/mihomo/releases/latest")
    if d and d.get("tag_name"):
        return d["tag_name"]

    # fallback: follow the redirect and read the tag out of the URL
    for target in _redirect_targets(
            "https://github.com/MetaCubeX/mihomo/releases/latest"):
        m = re.search(r"/releases/tag/(v?[\d.]+)", target)
        if m:
            return m.group(1)
    return ""


def _redirect_targets(url):
    """Ask curl where a URL redirects to, through the mirrors."""
    for m in P.MIRRORS:
        try:
            r = subprocess.run(
                ["curl.exe", "-s", "-o", "NUL", "-w", "%{url_effective}",
                 "-L", "-m", "20", "-I", m + url],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=28, creationflags=0x08000000)
            out = (r.stdout or "").strip()
            if out and "/releases/tag/" in out:
                yield out
        except Exception:
            continue


def _api_json(url):
    """
    GitHub API with three fallbacks, because on a mainland connection the
    plain API is unreachable and the proxy we would use is the thing that
    might not be running yet.

    Order: our own proxy (if up) -> mirror hosts -> straight api.github.com.
    """
    candidates = []
    if _core_up():
        candidates.append(("proxy", url))
    for m in P.MIRRORS[1:]:
        candidates.append(("mirror", m + url))
    candidates.append(("direct", url))

    for how, target in candidates:
        try:
            if how == "proxy":
                r = subprocess.run(
                    ["curl.exe", "-s", "-x", "http://" + P.PROXY_ADDR, "-m", "25",
                     "-H", "Accept: application/vnd.github+json",
                     url],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=32, creationflags=0x08000000)
                body = (r.stdout or "").strip()
                if not body:
                    continue
                return json.loads(body)
            req = urllib.request.Request(target, headers={
                "User-Agent": "NetGuard",
                "Accept": "application/vnd.github+json",
            })
            if P.GITHUB_TOKEN:
                req.add_header("Authorization", "token %s" % P.GITHUB_TOKEN)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            continue
    return None


def _same(a, b):
    """Compare tags ignoring the leading v and any pre-release suffix."""
    def norm(x):
        return re.sub(r"[^0-9.]", "", (x or "").lstrip("v"))
    return norm(a) == norm(b)


def backup(what="snapshot"):
    """
    Keep a few known-good copies. In ten years nobody remembers what the
    working configuration looked like, so the backup is the documentation.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)
    for src in (P.CONFIG_YAML, P.SOURCES_JSON, P.NODE_REPUTATION,
                P.SOURCE_STATS):
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(dest, os.path.basename(src)))
            except Exception:
                pass
    fast = os.path.join(P.SUBS, "nodes_fast.yaml")
    if os.path.exists(fast):
        try:
            shutil.copy2(fast, os.path.join(dest, "nodes_fast.yaml"))
        except Exception:
            pass
    # prune old ones
    snaps = sorted(d for d in os.listdir(BACKUP_DIR)
                   if os.path.isdir(os.path.join(BACKUP_DIR, d)))
    for old in snaps[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)
    _log("snapshot saved: %s" % stamp)
    return dest


def update_core(auto=True):
    """
    Keep the core current, but never at the cost of a working proxy.

    The new binary is unpacked beside the old one and tested on a spare port
    first. Only a core that passes its own config check AND answers a request
    replaces the live one.
    """
    have = _installed_version()
    latest = _latest_version()
    st = _load()
    st["core_version"] = have
    _save(st)
    if not latest:
        _log("could not reach the release feed; keeping the installed core")
        return False
    if not have:
        _log("no core installed")
        return False
    if _same(have, latest):
        return False

    _log("core %s installed, %s available" % (have, latest))
    if not auto:
        return False
    backup("before-core-upgrade")
    try:
        from intel import bootstrap
        if not bootstrap.update_mihomo():
            _log("upgrade failed; the previous core is untouched")
            return False
    except Exception as e:
        _log("upgrade error: %s" % e)
        return False

    new = _installed_version()
    ok, _ = _test_core()
    if not ok:
        _log("upgraded core rejects the config; rolling back")
        _rollback_core()
        return False
    _log("core upgraded to %s" % (new or latest))
    return True


def _rollback_core():
    prev = os.path.join(P.HOME, "core.prev")
    if os.path.exists(prev):
        try:
            shutil.copy2(prev, P.MIHOMO_EXE)
            _log("rolled back to the previous core")
            return True
        except Exception as e:
            _log("rollback failed: %s" % e)
    return False


# ---------------------------------------------------------------- sources

def source_health():
    """
    When every source has gone, do not keep retrying the dead ones -- go find
    new ones. Free repositories disappear; this is normal, not exceptional.
    """
    st = _load()
    last = st.get("source_crisis_at") or 0
    if time.time() - last < 6 * 3600:
        return False               # one discovery attempt every 6 hours is enough
    st["source_crisis_at"] = time.time()
    _save(st)
    _log("no usable source -- running discovery")
    try:
        from intel import discover
        found = discover.run()
        _log("discovery added %s sources" % found)
        return bool(found)
    except Exception as e:
        _log("discovery failed: %s" % e)
        return False


# ---------------------------------------------------------------- entry

def run(auto_update=True):
    """
    One pass. Called hourly by the tray; safe to call at any time.
    """
    st = _load()

    # 0. the filesystem itself. A user who cleaned out AppData, or a disk
    #    that came back empty, leaves us with nothing to work from -- and the
    #    core refuses to run at all without its config directory. Rebuild
    #    before anything else.
    P.ensure_dirs()
    for required in (P.CONFIG, P.SUBS, P.LOGS, P.MIHOMO_DIR):
        if not os.path.isdir(required):
            try:
                os.makedirs(required, exist_ok=True)
                _log("recreated missing directory: %s" % required)
            except Exception as e:
                _log("cannot create %s: %s" % (required, e))
    if not os.path.exists(P.SOURCES_JSON):
        P.seed()
        if os.path.exists(P.SOURCES_JSON):
            _log("source library was missing; restored from the bundled seed")
    if not os.path.exists(P.CST_EXE) or not os.path.exists(P.MIHOMO_EXE):
        try:
            from intel import bootstrap
            bootstrap.run(with_rulesets=False)
            _log("a core binary was missing; fetched it again")
        except Exception as e:
            _log("cannot fetch the missing binary: %s" % e)

    # 1. make sure we can still tell "up" from "down"
    if not st.get("health_urls"):
        url, code = pick_health_url()
        st["health_urls"] = [url]
        _save(st)
    elif not _health_ok(st["health_urls"][0]):
        url, code = pick_health_url(st.get("health_urls"))
        if url != st["health_urls"][0]:
            _log("health endpoint changed to %s" % url)
            st["health_urls"] = [url]
            _save(st)

    # 2. the core, kept current with a way back
    if auto_update:
        try:
            update_core(auto=True)
        except Exception as e:
            _log("core update check failed: %s" % e)

    # 3. snapshot before we touch anything
    try:
        backup()
    except Exception as e:
        _log("snapshot failed: %s" % e)

    return st


if __name__ == "__main__":
    run()