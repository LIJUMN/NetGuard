"""
record_yield.py — after every screening round, credit each source.

Reads the list of node names that passed the latency screen, maps them back
to fingerprints, maps fingerprints back to sources, and stores per-source
yield in source_stats.json. Over time this tells you which sources are
actually worth keeping and which are just noise.

This is the feature that changed how we think about the whole project: the
first run showed one source delivering 51 of 57 usable nodes while another
carried 4368 nodes and produced exactly one. Without that number we were
screening thousands of dead nodes on every run and did not know it.

IMPORTANT: run this BEFORE clash-speedtest renames the nodes. It matches on
the original names.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from intel import source_intel as si  # noqa: E402
from intel import manage_sources as ms  # noqa: E402


def record(wanted_names, label="run"):
    """
    wanted_names: iterable of node names that survived the latency screen
    returns: list of printable report lines
    """
    wanted = [w for w in wanted_names if isinstance(w, str) and w.strip()]
    wset = set(wanted)

    srcs = ms.load_sources()
    active = [s for s in srcs if s.get("enabled")]

    fp_of_name = {}
    source_of_fp = {}
    totals = {}
    for s in active:
        nodes = si.read_source(ms.path_of(s))
        name = s["name"]
        cnt = 0
        for n in nodes:
            nm = n.get("name")
            if not isinstance(nm, str):
                continue
            nm = nm.strip()
            fp = si.fingerprint(n)
            if not fp:
                continue
            cnt += 1
            fp_of_name.setdefault(nm, fp)
            source_of_fp.setdefault(fp, name)
        totals[name] = cnt

    alive_fp, unknown = set(), 0
    for nm in wset:
        fp = fp_of_name.get(nm.strip())
        if fp:
            alive_fp.add(fp)
        else:
            unknown += 1

    lines = []
    if not alive_fp:
        lines.append("no attributable nodes (unknown names: %d)" % unknown)
        lines.append("hint: run this BEFORE clash-speedtest renames the nodes")
        return lines

    si.stats_record(source_of_fp, alive_fp, totals)
    credited = {}
    for fp in alive_fp:
        nm = source_of_fp.get(fp)
        credited[nm] = credited.get(nm, 0) + 1

    lines.append("round %s: %d alive, %d attributed, %d unknown"
                 % (label, len(wset), len(alive_fp), unknown))
    lines.append("%-24s %7s %7s %8s" % ("source", "alive", "nodes", "yield"))
    rows = sorted(((credited.get(n, 0), n) for n in totals), reverse=True)
    for a, n in rows:
        t = totals.get(n, 0)
        lines.append("%-24s %7d %7d %7.2f%%"
                     % (n[:24], a, t, (a * 100.0 / t) if t else 0))
    return lines


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("usage: record_yield.py <alive_names.json> [round_label]")
        return 1
    names_file = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else "run"
    try:
        with open(names_file, encoding="utf-8") as fh:
            wanted = json.load(fh)
    except Exception as e:
        print("cannot read %s: %s" % (names_file, e))
        return 1
    if isinstance(wanted, dict):
        wanted = list(wanted.keys())
    lines = record(wanted, label)
    for ln in lines:
        print(ln)
    return 0 if not lines[0].startswith("no attributable") else 2


if __name__ == "__main__":
    sys.exit(main())