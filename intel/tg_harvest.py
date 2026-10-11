"""
tg_harvest.py — pull nodes out of Telegram public preview pages.

Why this exists: free-node sharing happens mostly on Telegram because
publishing there is instant and search engines do not index it. The public
preview at t.me/s/<channel> needs no login or API key.

Every harvested file goes through manage_sources.admit() before it is allowed
into the library, so a channel that just re-posts someone else's list is
detected as a re-post and rejected instead of bloating the source pool.

Usage:
  python tg_harvest.py <channel> [<channel> ...]
  python tg_harvest.py --seeds seeds.txt
"""
import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from intel import manage_sources as ms  # noqa: E402

CURL = r"C:\Windows\System32\curl.EXE"
PROXY = "http://127.0.0.1:7890"
SUBS = os.path.join(ms.si.SUBS)
TMP = os.path.join(os.environ.get("TEMP", "."), "tg_harvest")
os.makedirs(TMP, exist_ok=True)

LINK_RE = re.compile(r'(?:vmess|vless|trojan|ss)://[A-Za-z0-9+/=._\-%:@?&=#]+')


def _curl(url, out, timeout=40):
    subprocess.run([CURL, "-s", "-L", "-m", str(timeout), "-x", PROXY,
                    "-o", out, url], capture_output=True, timeout=timeout + 15)
    return os.path.exists(out) and os.path.getsize(out) > 200


def _b64(s):
    s = s.strip()
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except Exception:
        return ""


# ------------------------------------------------------------ link converters
def parse_vmess(link):
    raw = _b64(link.split("://", 1)[1])
    try:
        j = json.loads(raw)
    except Exception:
        return None
    add = j.get("add") or j.get("server")
    port = j.get("port")
    if not add or not port:
        return None
    host = j.get("host") or ""
    path = j.get("path") or "/"
    n = {
        "name": j.get("ps") or ("vmess-%s:%s" % (add, port)),
        "type": "vmess",
        "server": add,
        "port": int(port),
        "uuid": j.get("id") or "",
        "alterId": int(j.get("aid") or 0),
        "cipher": j.get("scy") or "auto",
        "tls": str(j.get("tls") or "") in ("tls", "true", "1"),
        "udp": True,
    }
    if host or path:
        n["network"] = j.get("net") or "ws"
        n["ws-opts"] = {"path": path}
        if host:
            n["ws-opts"]["headers"] = {"Host": host}
    return n


def parse_vless(link):
    try:
        body = link.split("://", 1)[1]
        if "@" not in body:
            return None
        cred, rest = body.split("@", 1)
        if "#" in rest:
            rest, frag = rest.split("#", 1)
        else:
            frag = ""
        qs = ""
        if "?" in rest:
            rest, qs = rest.split("?", 1)
        hostport = rest.split("/")[0]
        if ":" not in hostport:
            return None
        host, port = hostport.rsplit(":", 1)
        q = urllib.parse.parse_qs(qs)
        g = lambda k, d="": (q.get(k) or [d])[0]
        n = {
            "name": urllib.parse.unquote(frag) or ("vless-%s:%s" % (host, port)),
            "type": "vless",
            "server": host,
            "port": int(port),
            "uuid": urllib.parse.unquote(cred),
            "tls": g("security") == "tls",
            "udp": True,
            "skip-cert-verify": False,
        }
        if n["tls"] and g("sni"):
            n["servername"] = g("sni")
        net = g("type") or "tcp"
        if net in ("ws", "grpc"):
            n["network"] = net
            if net == "ws":
                n["ws-opts"] = {"path": g("path") or "/"}
                if g("host"):
                    n["ws-opts"]["headers"] = {"Host": g("host")}
            else:
                n["grpc-opts"] = {"grpc-service-name": g("serviceName") or ""}
        return n
    except Exception:
        return None


def parse_trojan(link):
    try:
        body = link.split("://", 1)[1]
        if "@" not in body:
            return None
        pw, rest = body.split("@", 1)
        frag = ""
        if "#" in rest:
            rest, frag = rest.split("#", 1)
        qs = ""
        if "?" in rest:
            rest, qs = rest.split("?", 1)
        hostport = rest.split("/")[0]
        if ":" not in hostport:
            return None
        host, port = hostport.rsplit(":", 1)
        q = urllib.parse.parse_qs(qs)
        g = lambda k, d="": (q.get(k) or [d])[0]
        n = {
            "name": urllib.parse.unquote(frag) or ("trojan-%s:%s" % (host, port)),
            "type": "trojan",
            "server": host,
            "port": int(port),
            "password": urllib.parse.unquote(pw),
            "tls": True,
            "udp": True,
            "skip-cert-verify": g("allowInsecure") in ("1", "true"),
        }
        if g("sni"):
            n["sni"] = g("sni")
        if g("type") in ("ws", "grpc"):
            n["network"] = g("type")
            if n["network"] == "ws":
                n["ws-opts"] = {"path": g("path") or "/"}
                if g("host"):
                    n["ws-opts"]["headers"] = {"Host": g("host")}
            else:
                n["grpc-opts"] = {"grpc-service-name": g("serviceName") or ""}
        return n
    except Exception:
        return None


def parse_ss(link):
    try:
        body = link.split("://", 1)[1]
        frag = ""
        if "#" in body:
            body, frag = body.split("#", 1)
        if "@" not in body:
            return None
        cred, hostport = body.rsplit("@", 1)
        hostport = hostport.split("?")[0]
        if ":" not in hostport:
            return None
        host, port = hostport.rsplit(":", 1)
        cred = _b64(cred) if ":" not in cred else cred
        if ":" not in cred:
            return None
        method, pw = cred.split(":", 1)
        n = {
            "name": urllib.parse.unquote(frag) or ("ss-%s:%s" % (host, port)),
            "type": "ss",
            "server": host,
            "port": int(port),
            "cipher": method,
            "password": pw,
            "udp": True,
        }
        return n
    except Exception:
        return None


PARSERS = {"vmess": parse_vmess, "vless": parse_vless,
           "trojan": parse_trojan, "ss": parse_ss}


def harvest_channel(ch):
    page = os.path.join(TMP, "%s.html" % re.sub(r"\W+", "_", ch))
    if not _curl("https://t.me/s/%s" % ch, page):
        return None
    html = open(page, encoding="utf-8", errors="replace").read()
    links = set(LINK_RE.findall(html))
    nodes, seen = [], set()
    for link in links:
        scheme = link.split("://", 1)[0].lower()
        fn = PARSERS.get(scheme)
        if not fn:
            continue
        n = fn(link)
        if not n:
            continue
        key = (n.get("server"), n.get("port"))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        nodes.append(n)
    return nodes


def write_yaml(name, nodes):
    path = os.path.join(SUBS, name + ".yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("proxies:\n")
        for n in nodes:
            fh.write("  - name: %s\n" % json.dumps(n["name"], ensure_ascii=False))
            for k, v in n.items():
                if k == "name":
                    continue
                if isinstance(v, dict):
                    fh.write("    %s: {%s}\n" % (k, ", ".join(
                        "%s: %s" % (kk, json.dumps(vv, ensure_ascii=False))
                        for kk, vv in v.items())))
                else:
                    fh.write("    %s: %s\n" % (k, json.dumps(v, ensure_ascii=False)))
    return path


POOL = "tg_pool"


def merge_into_pool(new_nodes):
    """
    Channels are small (3-15 nodes) but fresh; GitHub aggregators are huge but
    stale. So channels do NOT become sources of their own - they all merge into
    ONE rolling pool source. The pool grows over time and self-prunes by
    fingerprint, so the source count stays at +1 no matter how many channels we
    watch.
    """
    path = os.path.join(SUBS, POOL + ".yaml")
    existing = []
    if os.path.exists(path):
        import yaml as _y
        try:
            doc = _y.safe_load(open(path, encoding="utf-8", errors="replace")) or {}
            existing = doc.get("proxies") or []
        except Exception:
            existing = []
    seen = {(n.get("server"), n.get("port")) for n in existing if isinstance(n, dict)}
    added = 0
    for n in new_nodes:
        key = (n.get("server"), n.get("port"))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        existing.append(n)
        added += 1
    write_yaml(POOL, existing)
    return len(existing), added


def main():
    args = sys.argv[1:]
    if args and args[0] == "--seeds":
        with open(args[1], encoding="utf-8") as fh:
            channels = [l.strip().lstrip("@") for l in fh
                        if l.strip() and not l.startswith("#")]
    else:
        channels = [a.lstrip("@") for a in args]
    if not channels:
        print("no channels given")
        return 1

    total_new = 0
    pool_total = 0
    for ch in channels:
        nodes = harvest_channel(ch)
        if not nodes:
            print("  %-24s -> unreachable or empty" % ch)
            continue
        pool_total, added = merge_into_pool(nodes)
        total_new += added
        print("  %-24s -> %d nodes harvested, +%d into pool (pool now %d)"
              % (ch, len(nodes), added, pool_total))

    # the pool is a source like any other: same gate, but a low floor because
    # a rolling pool legitimately starts small
    if pool_total:
        ok, verdict, detail = ms.admit(POOL, os.path.join(SUBS, POOL + ".yaml"),
                                       min_nodes=5, reenable=True)
        if ok:
            print("pool in library (%s): %d nodes | %s" % (verdict, pool_total, detail))
        else:
            print("pool not admitted (%s): %s" % (verdict, detail))

    print("harvest done: new_unique=%d pool_size=%d" % (total_new, pool_total))
    return 0


if __name__ == "__main__":
    sys.exit(main())