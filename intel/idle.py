"""
Idle detection - do not steal bandwidth while the user is actively online.

Method: sample the mihomo traffic counters twice, 3s apart. If total
bytes moved in that window exceeds the threshold, the link is busy and
the caller should postpone.

CLI:  python idle.py            -> prints "IDLE <bytes>" or "BUSY <bytes>"
      python idle.py --wait N   -> poll up to N times (10s apart), exit 0
                                   when idle, 1 when still busy after N
"""
import os
import sys
import json
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as _paths  # noqa: E402

API = _paths.API + "/traffic"

# bytes / 3 seconds.  ~1.5 MB/s sustained == user is transferring something
THRESHOLD = 4_500_000


def sample(timeout=3):
    try:
        with urllib.request.urlopen(API, timeout=timeout) as r:
            line = r.readline().decode("utf-8", "replace")
        d = json.loads(line)
        return int(d.get("upTotal", 0)) + int(d.get("downTotal", 0))
    except Exception:
        return None


def busy_once(window=3):
    a = sample()
    if a is None:
        # core down: nothing is using the link through us
        return False, 0
    time.sleep(window)
    b = sample()
    if b is None:
        return False, 0
    moved = max(0, b - a)
    return moved > THRESHOLD, moved


def main():
    args = sys.argv[1:]
    if args and args[0] == "--wait":
        rounds = int(args[1]) if len(args) > 1 else 12
        for i in range(rounds):
            busy, moved = busy_once()
            if not busy:
                print("IDLE %d" % moved)
                return 0
            print("busy %.1f MB in %ds, waiting..." % (moved / 1048576.0, 3),
                  flush=True)
            time.sleep(10)
        print("BUSY")
        return 1
    busy, moved = busy_once()
    print(("BUSY %d" % moved) if busy else ("IDLE %d" % moved))
    return 1 if busy else 0


if __name__ == "__main__":
    sys.exit(main())