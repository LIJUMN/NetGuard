"""
speed_result.py — feed the bandwidth stage back into the reputation library.

Input: the file we handed to clash-speedtest (available.yaml) and the file it
produced (fast.yaml). Anything in the first but missing from the second failed
the speed filter; everything in the second survived with a measured speed.

That gives a clean alive/dead signal per node, which is what the reputation
store needs to learn over time.

Usage:
  python speed_result.py <available.yaml> <fast.yaml> [<round_label>]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from intel import source_intel as si  # noqa: E402

SPEED_RE = None


def load(path):
    import yaml
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8", errors="replace")) or {}
    except Exception:
        return []
    return [n for n in (doc.get("proxies") or []) if isinstance(n, dict)]


def measure_from_name(name):
    """clash-speedtest renames nodes to '... | 0.51MB/s' or '... | 450.3KB/s'."""
    if "|" not in name:
        return 0
    tail = name.split("|")[-1].strip()
    m = tail.replace("⬇️", "").strip()
    try:
        if m.endswith("MB/s"):
            return int(float(m[:-4].replace(",", "")) * 1024 * 1024)
        if m.endswith("KB/s"):
            return int(float(m[:-5].replace(",", "")) * 1024)
    except Exception:
        return 0
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    tested_path, passed_path = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else "run"

    tested = load(tested_path)
    passed = load(passed_path)
    if not tested:
        print("no tested nodes in %s" % tested_path)
        return 2

    passed_by_fp = {}
    for n in passed:
        fp = si.fingerprint(n)
        if fp:
            passed_by_fp[fp] = n

    results = {}
    for n in tested:
        fp = si.fingerprint(n)
        if not fp:
            continue
        hit = passed_by_fp.get(fp)
        if hit:
            sp = measure_from_name(hit.get("name") or "")
            if not sp:
                for key in ("speed", "download-speed"):
                    if n.get(key):
                        sp = int(n[key])
                        break
            results[fp] = {"alive": True, "speed": sp,
                           "name": hit.get("name") or n.get("name")}
        else:
            results[fp] = {"alive": False, "speed": 0,
                           "name": n.get("name")}

    total, blacklisted = si.rep_record_batch(results)

    alive = sum(1 for r in results.values() if r["alive"])
    print("round %s: %d tested, %d passed speed, %d failed" % (label, len(results), alive, len(results) - alive))
    print("reputation store: %d nodes tracked, %d newly blacklisted" % (total, blacklisted))
    black = si.rep_blacklisted()
    print("currently blacklisted (skipped next round): %d" % len(black))
    return 0


if __name__ == "__main__":
    sys.exit(main())