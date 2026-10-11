"""
extract_fast.py — resolve node names back into full node definitions.

The latency screen gives us a list of node names. To hand those to
clash-speedtest we need the whole node object (server, port, cipher,
credentials) back, which means re-reading the subscription files and
indexing them by name.

Names can collide across sources (two subscriptions may call different nodes
the same thing), so the first one wins -- which is fine because they are the
same node in every case we have measured.
"""
import yaml
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as _paths  # noqa: E402

SUBS = _paths.SUBS


def build_index(subs_dir=None):
    """name -> node dict, across every subscription file we have."""
    subs_dir = subs_dir or SUBS
    index = {}
    stats = []
    if not os.path.isdir(subs_dir):
        return index, [("(no subs dir)", 0, str(subs_dir))]
    for fn in sorted(os.listdir(subs_dir)):
        if not fn.endswith(".yaml"):
            continue
        path = os.path.join(subs_dir, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                doc = yaml.safe_load(fh)
        except Exception as e:
            stats.append((fn, 0, str(e)[:60]))
            continue
        if not isinstance(doc, dict):
            stats.append((fn, 0, "not a mapping"))
            continue
        nodes = doc.get("proxies")
        if not isinstance(nodes, list):
            stats.append((fn, 0, "no proxies list"))
            continue
        cnt = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nm = node.get("name")
            if not nm or not isinstance(nm, str):
                continue
            nm = nm.strip()
            if nm and nm not in index:
                index[nm] = node
                cnt += 1
        stats.append((fn, cnt, "ok"))
    return index, stats


def extract(wanted, out_file, verbose=False):
    """
    wanted: iterable of node names
    out_file: where to write the clash yaml
    returns: (picked_count, missing_count)
    """
    index, stats = build_index()
    if verbose:
        for fn, cnt, note in stats:
            print("  %-28s %6d  %s" % (fn, cnt, note))
        print("indexed nodes: %d" % len(index))

    picked, missing = [], []
    for n in wanted:
        if n in index:
            picked.append(index[n])
        else:
            missing.append(n)

    if verbose:
        print("picked %d / wanted %d" % (len(picked), len(wanted)))
        if missing:
            print("missing (first 5): %s" % missing[:5])

    if not picked:
        # An empty provider would silently break the whole proxy group.
        raise RuntimeError("nothing matched, refusing to write an empty provider")

    parent = os.path.dirname(os.path.abspath(out_file))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write("proxies:\n")
        yaml.safe_dump(picked, fh, allow_unicode=True,
                       default_flow_style=False, sort_keys=False)
    return len(picked), len(missing)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: extract_fast.py <wanted_names.json> <out.yaml>")
        return 1
    names_file, out_file = sys.argv[1], sys.argv[2]
    with open(names_file, encoding="utf-8") as fh:
        wanted = json.load(fh)
    if isinstance(wanted, dict):
        wanted = list(wanted.keys())
    try:
        picked, missing = extract(wanted, out_file, verbose=True)
    except RuntimeError as e:
        print("ERROR: %s" % e)
        return 2
    print("wrote %s with %d nodes" % (out_file, picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())