"""
Human-readable intelligence reports.

The tray menu needs something to show when you click "source quality" or
"node reputation". This renders the JSON stores into plain text files that
open in Notepad.

CLI:  python report.py            -> writes all reports, prints paths
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as _paths  # noqa: E402

PROXY = _paths.CONFIG
MIH = _paths.PIPELINE
OUT = _paths.REPORTS
STATS = _paths.SOURCE_STATS
SRCJ = _paths.SOURCES_JSON
NREP = _paths.NODE_REPUTATION
STATE = _paths.TRAY_STATE

os.makedirs(OUT, exist_ok=True)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def source_report():
    st = _load(STATS, {})
    srcs = _load(SRCJ, [])
    enabled = {s.get("name"): bool(s.get("enabled")) for s in srcs}

    lines = ["=" * 62,
             "  SOURCE QUALITY REPORT      %s" % time.strftime("%Y-%m-%d %H:%M"),
             "=" * 62, ""]
    lines.append("  %-22s %7s %7s %9s %6s" %
                 ("source", "alive", "nodes", "yield", "state"))
    lines.append("  " + "-" * 58)
    rows = []
    for name, e in st.items():
        nodes = e.get("nodes", 0)
        y = e.get("avg_yield", 0.0)
        rows.append((y, name, e.get("alive", 0), nodes, e.get("runs", 0)))
    rows.sort(reverse=True)
    for y, name, alive, nodes, runs in rows:
        flag = "on" if enabled.get(name, True) else "OFF"
        lines.append("  %-22s %7d %7d %8.2f%% %6s" %
                     (name[:22], alive, nodes, y * 100, flag))
    if not rows:
        lines.append("  (no data yet - run the pipeline once)")

    # verdict
    lines += ["", "  VERDICT", "  " + "-" * 58]
    good = [r for r in rows if r[0] >= 0.05]
    mid = [r for r in rows if 0.005 <= r[0] < 0.05]
    bad = [r for r in rows if r[0] < 0.005]
    lines.append("  high yield (>5%%)  : %s" % (", ".join(r[1] for r in good) or "-"))
    lines.append("  medium(0.5-5%%)  : %s" % (", ".join(r[1] for r in mid) or "-"))
    lines.append("  low (<0.5%%)      : %s" % (", ".join(r[1] for r in bad) or "-"))
    if bad:
        lines += ["", "  NOTE: low-yield sources still load nodes on every run.",
                  "  Pruning happens automatically once overlap confirms",
                  "  they are re-posts. Low yield alone is not enough to drop",
                  "  a source - it needs more rounds of evidence."]
    lines.append("")
    return "\n".join(lines)


def node_report():
    rep = _load(NREP, {})
    lines = ["=" * 62,
             "  NODE REPUTATION REPORT    %s" % time.strftime("%Y-%m-%d %H:%M"),
             "=" * 62, ""]
    if not rep:
        lines.append("  (empty - the library needs 3 pipeline runs before it")
        lines.append("   means anything. Runs 1-2 just collect evidence.)")
    else:
        now = time.time()
        rows = []
        for fp, e in rep.items():
            if not isinstance(e, dict):
                continue
            seen = e.get("seen", 0)
            ok = e.get("alive", 0)
            rate = ok / seen if seen else 0.0
            # score blends reliability with measured speed, penalising one-offs
            score = rate * 100 + min(e.get("speed", 0) / 1048576.0, 20) * 2
            if e.get("black", 0) > now:
                score -= 50
            rows.append((score, rate, fp, e))
        rows.sort(reverse=True)
        lines.append("  %-32s %5s %5s %6s %9s" %
                     ("node", "seen", "ok", "rate", "speed"))
        lines.append("  " + "-" * 58)
        for score, rate, fp, e in rows[:30]:
            sp = e.get("speed", 0)
            lines.append("  %-32s %5d %5d %5.0f%% %8s" % (
                (e.get("last_name") or fp)[:32],
                e.get("seen", 0), e.get("alive", 0), rate * 100,
                ("%.2fMB/s" % (sp / 1048576.0)) if sp else "-"))
        if len(rows) > 30:
            lines.append("  ... and %d more" % (len(rows) - 30))
        black = [r for r in rows if r[3].get("black", 0) > now]
        lines += ["", "  tracked      : %d nodes" % len(rows),
                  "  blacklisted  : %d (skipped for 7 days)" % len(black)]
    lines.append("")
    return "\n".join(lines)


def overview():
    st = _load(STATE, {})
    lines = ["=" * 62,
             "  SYSTEM OVERVIEW           %s" % time.strftime("%Y-%m-%d %H:%M"),
             "=" * 62, ""]
    lines.append("  state        : %s" % st.get("state", "?"))
    lines.append("  message      : %s" % st.get("msg", "?"))
    lines.append("  node         : %s" % st.get("node", "-"))
    lines.append("  live nodes   : %s / %s" % (st.get("alive", 0), st.get("total", 0)))
    lines.append("  fast set     : %s" % st.get("fast", "-"))
    lines.append("  last run     : %s (%ss)" % (st.get("ts", "-"), st.get("elapsed", "-")))
    lines += ["", "  SOURCE LIBRARY", "  " + "-" * 58]
    srcs = _load(SRCJ, [])
    on = [s for s in srcs if s.get("enabled")]
    lines.append("  %d enabled / %d total" % (len(on), len(srcs)))
    for s in on:
        lines.append("    - %s" % s.get("name"))
    lines.append("")
    return "\n".join(lines)


def write_all():
    """
    Render every report to disk and return the paths written.

    Split out of main() so the pipeline can call it directly. The pipeline used
    to call source_report()/node_report() and assume that produced files -- those
    functions only RETURN text. Nothing was written, nothing raised, and the run
    logged "reports written to ..." while the directory stayed empty. A report
    step that cannot fail is not a report step.

    Raises on failure so the caller sees it.
    """
    made = []
    for name, fn in (("source_quality", source_report),
                     ("node_reputation", node_report),
                     ("overview", overview)):
        p = os.path.join(OUT, "%s.txt" % name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(fn())
        made.append(p)
    return made


def main():
    try:
        made = write_all()
    except Exception as e:
        print("  report generation failed: %s" % e)
        return 1
    for p in made:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())