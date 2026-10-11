"""Strip a clash-speedtest output config down to a bare proxies list.

mihomo's proxy-provider only accepts a file whose top level is `proxies:`
(or a raw subscription). clash-speedtest writes `proxy-providers` too,
which makes mihomo silently drop the provider.
"""
import sys
import yaml


def main():
    if len(sys.argv) < 3:
        print("usage: clean_fast.py <in.yaml> <out.yaml>")
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8", errors="replace") as fh:
        doc = yaml.safe_load(fh) or {}
    nodes = doc.get("proxies") or []
    if not nodes:
        print("no proxies found, refuse to overwrite")
        return 2
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("proxies:\n")
        yaml.safe_dump(nodes, fh, allow_unicode=True,
                       default_flow_style=False, sort_keys=False)
    print("wrote %d nodes" % len(nodes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
