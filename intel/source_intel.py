"""
source_intel.py — the intelligence layer.

Four capabilities in one module, because they all read the same thing
(node fingerprints) and must stay consistent with each other:

  1. fingerprint()        build a stable identity for a node
  2. dedupe / overlap     decide whether a "new" source is real or a re-post
  3. reputation           remember which nodes actually work
  4. scoring              remember which sources actually deliver

Data files (all under proxy/):
  source_stats.json    per-source yield history
  node_reputation.json per-node survival record
  source_index.json    fingerprint sets for every known source

Run standalone to inspect:
  python source_intel.py stats
  python source_intel.py rank-sources
  python source_intel.py rank-nodes
"""
import json
import os
import re
import sys
import time
import hashlib
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as _paths  # noqa: E402

PROXY = _paths.CONFIG
SUBS = _paths.SUBS
STATS = _paths.SOURCE_STATS
REPUT = _paths.NODE_REPUTATION
SINDEX = _paths.SOURCE_INDEX
CUSTOM = _paths.CUSTOM_SUBS

# Marker for sources the user added by hand. They are exempt from pruning:
# the whole point of the file is that a source the user chose outranks what an
# overlap heuristic thinks of it.
CUSTOM_MARK = "custom"


def custom_sources():
    """
    Read the user's own subscription list.

    Format is deliberately brain-dead, because it has to be editable on a
    machine with no tooling:

        # anything after a hash is ignored
        https://example.com/my-subscription
        我的机场 = https://example.com/sub?token=abc

    A bare URL is named customN from its position; "name = url" lets the user
    choose. Returns a list of dicts in the same shape as sources.json so the
    rest of the pipeline needs no special case.
    """
    out = []
    path = CUSTOM
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return out

    # Numbered by position among the ENTRIES, not by file line. The template
    # that ships in this file is twenty-odd lines of comments, so a line-based
    # count named the first real subscription "custom26" -- harmless, but it
    # makes the source list look broken and hard to talk about.
    idx = 0
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line and not line.lower().startswith("http"):
            name, _, url = line.partition("=")
            name, url = name.strip(), url.strip()
        else:
            name, url = "", line
        if not url.lower().startswith(("http://", "https://")):
            continue                     # not a URL, most likely a stray line
        idx += 1
        if not name:
            name = "custom%d" % idx
        name = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff-]", "_", name)[:32] or \
            "custom%d" % idx
        out.append({
            "name": name,
            "url": url,
            "enabled": True,
            "fails": 0,
            "added": CUSTOM_MARK,
            "priority": True,            # the pipeline reads this to prefer it
        })
    return out

# tuning
BLACKLIST_AFTER = 3# consecutive failures before blacklisting
BLACKLIST_SECONDS = 7 * 24 * 3600
REINSTATE_AFTER = 2        # consecutive successes to un-blacklist
REDUNDANCY_RATIO = 0.80    # >80% duplicate -> treat as re-post, skip


# --------------------------------------------------------------- io helpers
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def fingerprint(node):
    """
    Stable identity for a node.

    server:port alone is too coarse - two different nodes can share a port
    (common on multiplexed relays), and the same node moved to another port
    looks brand new. So we mix in the credential material: uuid / password /
    cipher. Renaming a node (clash-speedtest does that) never changes these.
    """
    if not isinstance(node, dict):
        return None
    srv = node.get("server")
    port = node.get("port")
    if srv and port:
        cred = ""
        for k in ("uuid", "password", "cipher"):
            v = node.get(k)
            if isinstance(v, str) and v:
                cred = v.strip().lower()
                break
        raw = "%s:%s:%s" % (str(srv).strip().lower(), port, cred[:40])
    else:
        raw = "%s|%s" % (node.get("name", ""), node.get("type", ""))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


LINK_RE = re.compile(r'(?:vmess|vless|trojan|ss|ssr|hysteria2?)://[A-Za-z0-9+/=._\-%:@?&\=#\[\]]+')


def read_source(path):
    """
    Return a list of node dicts from either a clash yaml file or a raw
    base64 subscription (vmess:// / vless:// / trojan:// / ss:// links).
    """
    import base64 as _b64
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return []
    try:
        doc = yaml.safe_load(text)
    except Exception:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("proxies"), list):
        return [n for n in doc["proxies"] if isinstance(n, dict)]
    if isinstance(doc, list) and doc and isinstance(doc[0], dict):
        return doc

    # not yaml -> maybe base64 subscription
    compact = re.sub(r'\s', '', text)
    if len(compact) < 20:
        return []
    try:
        dec = _b64.b64decode(compact + "=" * (-len(compact) % 4))
        links = LINK_RE.findall(dec.decode("utf-8", "ignore"))
    except Exception:
        return []
    if not links:
        return []

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import tg_harvest as th
    except Exception:
        return []
    out = []
    for link in links:
        fn = th.PARSERS.get(link.split("://", 1)[0].lower())
        if fn:
            n = fn(link)
            if n:
                out.append(n)
    return out


# --------------------------------------------------------------- source index
def build_index(sources):
    """sources: list of dicts with name/path. Returns {name: set(fp)}."""
    idx = {}
    for s in sources:
        name = s.get("name")
        path = s.get("path") or os.path.join(SUBS, name + ".yaml")
        fps = set()
        for n in read_source(path):
            fp = fingerprint(n)
            if fp:
                fps.add(fp)
        if fps:
            idx[name] = sorted(fps)
    _save(SINDEX, idx)
    return idx


def overlap(new_fps, existing):
    """Return (ratio, dup_count) of new_fps against every existing source."""
    best_ratio, best_dup, best_name = 0.0, 0, None
    new_set = set(new_fps)
    if not new_set:
        return 1.0, 0, None
    for name, fps in existing.items():
        old = set(fps)
        if not old:
            continue
        dup = len(new_set & old)
        if dup == 0:
            continue
        ratio = dup / float(len(new_set))
        if ratio > best_ratio:
            best_ratio, best_dup, best_name = ratio, dup, name
    return best_ratio, best_dup, best_name


def judge_new_source(new_fps, existing, threshold=REDUNDANCY_RATIO):
    """Return (verdict, detail). verdict in {new, partial, repost}."""
    ratio, dup, name = overlap(new_fps, existing)
    if ratio == 0.0:
        return "new", "0%% overlap"
    if ratio >= threshold:
        return "repost", "%d nodes, %.0f%% duplicate of %s" % (len(new_fps), ratio * 100, name)
    return "partial", "%d nodes, %.0f%% duplicate of %s" % (len(new_fps), ratio * 100, name)


# --------------------------------------------------------------- reputation
def rep_get():
    return _load(REPUT, {})


def rep_record_batch(results, nodes_by_fp=None):
    """
    results: dict fp -> {'alive': bool, 'speed': int}
    Returns (updated_count, newly_blacklisted)
    """
    rep = rep_get()
    now = time.time()
    blacklisted = 0
    for fp, r in results.items():
        e = rep.setdefault(fp, {"seen": 0, "alive": 0, "streak_fail": 0,
                                "streak_ok": 0, "speed": 0, "black": 0,
                                "last_seen": 0, "last_name": ""})
        e["seen"] += 1
        e["last_seen"] = now
        if r.get("name"):
            e["last_name"] = r["name"]
        if r.get("alive"):
            e["alive"] += 1
            e["streak_ok"] += 1
            e["streak_fail"] = 0
            if r.get("speed"):
                e["speed"] = int(r["speed"])
            if e.get("black") and e["streak_ok"] >= REINSTATE_AFTER:
                e["black"] = 0
        else:
            e["streak_fail"] += 1
            e["streak_ok"] = 0
            if (not e.get("black")) and e["streak_fail"] >= BLACKLIST_AFTER:
                e["black"] = now + BLACKLIST_SECONDS
                blacklisted += 1
    _save(REPUT, rep)
    return len(rep), blacklisted


def rep_blacklisted():
    rep = rep_get()
    now = time.time()
    return {fp for fp, e in rep.items() if e.get("black", 0) > now}


def rep_priority_names():
    """Node names worth testing first: previously alive, not blacklisted."""
    rep = rep_get()
    now = time.time()
    out = []
    for fp, e in rep.items():
        if e.get("black", 0) > now:
            continue
        if e.get("alive", 0) > 0 and e.get("last_name"):
            out.append((e.get("alive", 0), e.get("speed", 0), e["last_name"]))
    out.sort(reverse=True)
    return [n for _, _, n in out]


# --------------------------------------------------------------- source score
def stats_get():
    return _load(STATS, {})


def stats_record(source_of_fp, alive_fps, total_by_source):
    """
    source_of_fp: fp -> source name
    alive_fps: iterable of fps that passed the latency screen
    total_by_source: source name -> node count in that source file
    """
    st = stats_get()
    alive_per = {}
    for fp in alive_fps:
        s = source_of_fp.get(fp)
        if s:
            alive_per[s] = alive_per.get(s, 0) + 1
    for name, total in total_by_source.items():
        e = st.setdefault(name, {"runs": 0, "alive": 0, "nodes": 0,
                                 "best": 0, "history": []})
        a = alive_per.get(name, 0)
        e["runs"] += 1
        e["alive"] = a
        e["nodes"] = total
        e["best"] = max(e.get("best", 0), a)
        e["history"].append(a)
        e["history"] = e["history"][-10:]
        e["yield"] = round(a / float(total), 5) if total else 0.0
        # average the ALIVE COUNT across runs first, then divide once --
        # averaging the ratios directly inflates the number
        e["avg_alive"] = round(sum(e["history"]) / float(len(e["history"])), 2)
        e["avg_yield"] = round(e["avg_alive"] / float(total), 5) if total else 0.0
    _save(STATS, st)
    return st


def rank_sources():
    st = stats_get()
    rows = []
    for name, e in st.items():
        if not e.get("runs"):
            continue
        rows.append((e.get("avg_yield", 0), e.get("best", 0), name, e))
    rows.sort(reverse=True)
    return rows


def prune_redundant(threshold=REDUNDANCY_RATIO, keep_min=4):
    """
    Mark sources that are (near) duplicates of a bigger one as aliases.

    Returns (kept, aliased, report). Order matters: the biggest source wins,
    so a small subset never evicts the superset it lives inside.
    """
    idx = _load(SINDEX, {})
    names = sorted(idx, key=lambda k: -len(idx[k]))
    kept, aliased, report = [], {}, []
    for name in names:
        if len(kept) < keep_min:
            kept.append(name)
            continue
        others = {k: v for k, v in idx.items() if k in kept}
        ratio, dup, winner = overlap(idx[name], others)
        if ratio >= threshold:
            aliased[name] = {"duplicate_of": winner, "overlap": round(ratio, 3),
                             "nodes": len(idx[name])}
            report.append("%-22s -> alias of %s (%.0f%% dup, %d nodes dropped)"
                          % (name[:22], winner, ratio * 100, len(idx[name])))
        else:
            kept.append(name)
            report.append("%-22s kept (%.0f%% overlap, %d unique)"
                          % (name[:22], ratio * 100, len(idx[name]) - dup))
    return kept, aliased, report


# --------------------------------------------------------------- cli
def _cmd_stats():
    st = stats_get()
    if not st:
        print("no source stats yet")
        return
    print("%-24s %6s %8s %8s %8s" % ("source", "runs", "nodes", "alive", "yield"))
    for y, b, name, e in rank_sources():
        print("%-24s %6d %8d %8d %7.2f%%" %
              (name[:24], e["runs"], e.get("nodes", 0), e.get("alive", 0), y * 100))


def _cmd_nodes():
    rep = rep_get()
    now = time.time()
    live = [(e.get("alive", 0), e.get("speed", 0), fp, e)
            for fp, e in rep.items() if e.get("black", 0) <= now]
    live.sort(reverse=True)
    print("%-18s %5s %5s %6s %10s  %s" % ("fp", "seen", "alive", "black", "speed", "name"))
    for a, sp, fp, e in live[:25]:
        print("%-18s %5d %5d %6s %10s  %s" %
              (fp, e["seen"], a, "yes" if e.get("black", 0) > now else "-",
               ("%.0fKB/s" % (sp / 1024)) if sp else "-", e.get("last_name", "")[:34]))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        _cmd_stats()
    elif cmd == "rank-nodes":
        _cmd_nodes()
    else:
        print(__doc__)