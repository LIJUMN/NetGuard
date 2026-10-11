"""
manage_sources.py — keeps the source library honest.

Runs as part of the daily pipeline:
  1. rebuild the fingerprint index from every enabled source
  2. mark near-duplicate sources as aliases (they cost time, add nothing)
  3. persist alias info into sources.json so mihomo stops loading them
  4. write a human-readable report for the tray menu to show

Also the gate used by discover: a candidate source must clear
judge_new_source() before it is allowed into the library.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from intel import source_intel as si  # noqa: E402

PROXY = si.PROXY
SRCJ = os.path.join(PROXY, "sources.json")
REPORT = os.path.join(PROXY, "source_report.json")


def load_sources():
    """
    The source library: everything in sources.json, plus whatever the user put
    in custom_subscriptions.txt.

    The custom file is merged on every load rather than imported once. That way
    adding a line to a text file is the entire install procedure -- no command
    to run, no state to keep in sync -- and removing the line removes the
    source. Editing a file is a thing a user can do in ten years; remembering a
    subcommand is not.
    """
    raw = si._load(SRCJ, [])
    srcs = raw if isinstance(raw, list) else []
    try:
        for c in si.custom_sources():
            hit = None
            for s in srcs:
                if (s.get("url") or "").strip() == c["url"]:
                    hit = s
                    break
            if hit:
                # already known: make sure it is on, and keep the priority flag
                hit["enabled"] = True
                hit["priority"] = True
                hit["added"] = si.CUSTOM_MARK
            else:
                srcs.append(c)
    except Exception:
        pass
    return srcs


def save_sources(srcs):
    si._save(SRCJ, srcs)


def path_of(src):
    p = src.get("path")
    if p:
        return p if os.path.isabs(p) else os.path.join(si.SUBS, p)
    return os.path.join(si.SUBS, src["name"] + ".yaml")


def refresh():
    """Rebuild index + prune. Returns (srcs, report_dict)."""
    srcs = load_sources()
    enabled = [s for s in srcs if s.get("enabled")]
    if not enabled:
        return srcs, {"error": "no enabled sources"}

    idx = si.build_index([{"name": s["name"], "path": path_of(s)} for s in enabled])
    missing = [s["name"] for s in enabled if s["name"] not in idx]
    kept, aliased, lines = si.prune_redundant()

    for s in srcs:
        n = s["name"]
        # A source the user added by hand is never auto-disabled. The overlap
        # heuristic exists to stop DISCOVERY from flooding the library with
        # re-posts of the same free pool; applying it to the user's own
        # subscription would let a heuristic throw away the one source that was
        # deliberately chosen. If they added it, it stays.
        if s.get("added") == si.CUSTOM_MARK or s.get("priority"):
            s["enabled"] = True
            s.pop("alias_of", None)
            s.pop("overlap", None)
        elif n in aliased:
            s["enabled"] = False
            s["alias_of"] = aliased[n]["duplicate_of"]
            s["overlap"] = aliased[n]["overlap"]
        elif n in kept:
            s.pop("alias_of", None)
            s.pop("overlap", None)

    save_sources(srcs)

    total_before = sum(len(v) for v in idx.values())
    total_after = sum(len(idx.get(k, [])) for k in kept)
    rep = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources_total": len(srcs),
        "sources_active": len([s for s in srcs if s.get("enabled")]),
        "aliased": aliased,
        "unparsed": missing,
        "lines": lines,
        "nodes_before": total_before,
        "nodes_after": total_after,
        "nodes_dropped": total_before - total_after,
    }
    si._save(REPORT, rep)
    return srcs, rep


def admit(candidate_name, candidate_path, threshold=si.REDUNDANCY_RATIO,
          min_nodes=30, reenable=False):
    """
    Gate for newly discovered sources.
    Returns (admitted: bool, verdict: str, detail: str).

    min_nodes is lowered for rolling pools (e.g. the Telegram pool) that are
    small on day one but accumulate every day.
    reenable lets an existing-but-disabled source come back once it has grown.
    """
    fps = set()
    for n in si.read_source(candidate_path):
        fp = si.fingerprint(n)
        if fp:
            fps.add(fp)
    if len(fps) < min_nodes:
        return False, "tiny", "only %d usable nodes (need %d)" % (len(fps), min_nodes)

    existing = si._load(si.SINDEX, {})
    verdict, detail = si.judge_new_source(sorted(fps), existing, threshold)
    if verdict == "repost":
        return False, "repost", detail

    srcs = load_sources()
    for s in srcs:
        if s["name"] == candidate_name:
            if reenable and not s.get("enabled"):
                s["enabled"] = True
                s.pop("alias_of", None)
                save_sources(srcs)
                return True, "re-enabled", detail
            return False, "exists", "name already in library"
    srcs.append({
        "name": candidate_name,
        "url": "",                       # filled by the caller when it has a URL
        "enabled": True,
        "fails": 0,
        "added": time.strftime("%Y-%m-%d"),
        "admitted_by": "discover",
        "verdict": verdict,
        "detail": detail,
        "unique_nodes": len(fps),
    })
    save_sources(srcs)
    return True, verdict, detail


def show():
    rep = si._load(REPORT, None)
    if not rep:
        print("no report yet")
        return
    print("updated : %s" % rep["ts"])
    print("active  : %d / %d sources" % (rep["sources_active"], rep["sources_total"]))
    print("nodes   : %d -> %d (dropped %d)"
          % (rep["nodes_before"], rep["nodes_after"], rep["nodes_dropped"]))
    if rep.get("unparsed"):
        print("unparsed (base64 or malformed): %s" % ", ".join(rep["unparsed"]))
    for l in rep.get("lines", []):
        print("   ", l)
    print()
    srcs = [s for s in load_sources() if s.get("enabled")]
    print("=== source yield ranking ===")
    _cmd = si.rank_sources()
    if _cmd:
        for y, b, name, e in _cmd:
            print("  %-24s yield %5.2f%%  best %d  (runs %d)"
                  % (name[:24], y * 100, b, e["runs"]))
    else:
        print("  (no yield data yet - run the pipeline a couple of times)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "refresh"
    if cmd == "refresh":
        _, rep = refresh()
        for l in rep.get("lines", []):
            print("  ", l)
        print("active %d/%d, nodes %d -> %d"
              % (rep["sources_active"], rep["sources_total"],
                 rep["nodes_before"], rep["nodes_after"]))
    elif cmd == "show":
        show()
    elif cmd == "admit" and len(sys.argv) >= 4:
        ok, verdict, detail = admit(sys.argv[2], sys.argv[3])
        print("admitted=%s verdict=%s detail=%s" % (ok, verdict, detail))
    else:
        print(__doc__)