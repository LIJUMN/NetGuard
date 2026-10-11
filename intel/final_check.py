"""
final_check.py — end-to-end verification of split routing and purpose groups.

Run manually when you want to see what the config actually does, as opposed
to what it says it does.
"""
import json
import time
import urllib.request
import subprocess

API = "http://127.0.0.1:9090"
PROXY = "http://127.0.0.1:7890"
HIDE = 0x08000000


def probe(url, label, proxy=True, timeout=25, extra=None):
    cmd = ["curl.exe", "-s", "-o", "NUL", "-m", str(timeout),
           "-w", "%{http_code}|%{time_total}|%{speed_download}"]
    if proxy:
        cmd += ["-x", PROXY]
    if extra:
        cmd += extra
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout + 10, creationflags=HIDE)
    out = (r.stdout or "").strip()
    parts = out.split("|")
    code = parts[0] if parts else "000"
    secs = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
    spd = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    return code, secs, spd


def group(name):
    return json.loads(urllib.request.urlopen(API + "/proxies/" + name,
                                             timeout=8).read().decode("utf-8"))


def switch(name):
    body = json.dumps({"name": name}).encode("utf-8")
    urllib.request.urlopen(urllib.request.Request(
        API + "/proxies/PROXY", data=body, method="PUT",
        headers={"Content-Type": "application/json"}), timeout=8)


def main():
    """Run the checks. Kept inside a function so importing this module does
    not fire several minutes of network traffic at an innocent caller."""
    print("=" * 58)
    print("  1. DOMESTIC -- expect direct and fast")
    print("=" * 58)
    for u, l in [("https://www.baidu.com", "baidu"),
                 ("https://www.taobao.com", "taobao"),
                 ("https://www.bilibili.com", "bilibili"),
                 ("https://www.163.com", "netease")]:
        c, s, _ = probe(u, l)
        print("   %-11s %-5s %6.2fs" % (l, c, s))
    
    print()
    print("=" * 58)
    print("  2. FOREIGN -- expect to go through the proxy")
    print("=" * 58)
    for u, l in [("https://www.google.com/generate_204", "google"),
                 ("https://github.com", "github"),
                 ("https://stackoverflow.com", "stackovr"),
                 ("https://en.wikipedia.org", "wikipedia")]:
        c, s, _ = probe(u, l)
        print("   %-11s %-5s %6.2fs" % (l, c, s))
    
    print()
    print("=" * 58)
    print("  3. PURPOSE GROUPS -- measure each group's current pick")
    print("=" * 58)
    url = "https://speed.cloudflare.com/__down?bytes=3000000"
    for g in ("WEB", "DOWN", "MEDIA", "FAST", "AUTO"):
        try:
            # Ask each group which node IT would use, rather than forcing PROXY
            # over to it. Switching PROXY only re-points the selector; the
            # url-test inside keeps its own choice until its next interval.
            gdata = group(g)
            pick = gdata.get("now")
            info = gdata.get("history") or []
            switch(g)
            time.sleep(1.5)
            c, s, spd = probe(url, g, timeout=30)
            print("   %-7s picks=%-30s %-4s %5.2f MB/s"
                  % (g, str(pick)[:30], c, spd / 1048576.0))
        except Exception as e:
            print("   %-7s error: %s" % (g, e))
    
    switch("FAST")
    print("\n   PROXY reset to FAST")
    print("=" * 58)


if __name__ == "__main__":
    main()
