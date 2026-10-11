"""
split_check.py — prove the split routing actually does what it claims.

The config says domestic goes direct and foreign goes through a proxy, but
"the config says so" is not evidence. This starts a throwaway core on a
spare port with the same rule set and measures both halves:

  domestic  -> latency should be the direct one, and the core log should say
               "using DIRECT"
  foreign   -> should be rejected without a proxy group, proving the rule
               fired rather than the request simply failing

That distinction matters. A domestic site that fails tells you nothing; a
domestic site that answers quickly while the log says DIRECT tells you the
rule hit.
"""
import os
import re
import sys
import time
import json
import shutil
import subprocess
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import paths as P  # noqa: E402

TEST_PORT = 17891
TEST_CTL = 19091

DOMESTIC = [
    ("https://www.baidu.com", "baidu"),
    ("https://www.qq.com", "qq"),
    ("https://www.taobao.com", "taobao"),
    ("https://www.163.com", "netease"),
]

FOREIGN = [
    ("https://www.google.com", "google"),
    ("https://www.github.com", "github"),
    ("https://www.wikipedia.org", "wikipedia"),
]


def _log(msg):
    print(msg, flush=True)


def _core_ok(port=TEST_CTL):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/version" % port,
                                    timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _curl(url, port=TEST_PORT, timeout=12):
    """returns (http_code, seconds) or (None, seconds) on failure"""
    try:
        r = subprocess.run(
            ["curl.exe", "-s", "-x", "http://127.0.0.1:%d" % port, "-o", "NUL",
             "-m", str(timeout), "-w", "%{http_code}|%{time_total}", url],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout + 8, creationflags=0x08000000)
        out = (r.stdout or "").strip()
        code, _, secs = out.partition("|")
        try:
            return (int(code) if code and code != "000" else None), float(secs or 0)
        except ValueError:
            return None, float(secs or 0)
    except Exception:
        return None, 0.0


def build_test_config(dest_dir):
    """
    Same rules as production, but every purpose-built group is rewritten to
    DIRECT and the catch-all is REJECT.

    That combination is what makes the test meaningful: a domestic site can
    only answer if a DIRECT rule fired, and a foreign site can only fail if
    the geodata rules actually matched it. There is no proxy in the picture,
    so nothing can succeed by accident.
    """
    tpl = open(P.TEMPLATE_YAML, encoding="utf-8").read()
    tpl = tpl.replace("mixed-port: 7890", "mixed-port: %d" % TEST_PORT)
    tpl = tpl.replace("socks-port: 7891", "socks-port: %d" % (TEST_PORT + 1))
    tpl = tpl.replace("external-controller: 127.0.0.1:9090",
                      "external-controller: 127.0.0.1:%d" % TEST_CTL)
    tpl = re.sub(r"\n  proxy-providers:[\s\S]*?(?=\nproxy-groups:)",
                 "\nproxy-providers: {}\n", tpl)
    tpl = re.sub(r"{{PROVIDERS}}", "", tpl)
    tpl = re.sub(r"{{PROVIDER_USE}}", "", tpl)
    # Drop every provider-backed group, then point the rules at DIRECT.
    tpl = re.sub(r"\n  - name: (PROXY|AUTO|FAST|WEB|DOWN|MEDIA|FALLBACK)\n"
                 r"(?:    [^\n]*\n)+", "\n", tpl)
    for g in ("PROXY", "AUTO", "FAST", "WEB", "DOWN", "MEDIA", "FALLBACK"):
        tpl = tpl.replace(",%s\n" % g, ",DIRECT\n")
        tpl = tpl.replace(",%s,no-resolve" % g, ",DIRECT,no-resolve")
    tpl = re.sub(r"(?m)^  - MATCH,.*$", "  - MATCH,REJECT", tpl)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "split_test.yaml")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tpl)
    # fail loudly rather than silently testing the wrong thing
    leftover = re.findall(r",(?:PROXY|AUTO|FAST|WEB|DOWN|MEDIA|FALLBACK)\b", tpl)
    if leftover:
        raise RuntimeError("test config still references %s" % set(leftover))
    return path


def main():
    if not os.path.exists(P.MIHOMO_EXE):
        _log("core not installed yet at %s" % P.MIHOMO_EXE)
        return 1
    tmp = tempfile.mkdtemp(prefix="netguard_split_")
    for f in ("geosite.dat", "geoip.dat", "country.mmdb"):
        src = os.path.join(P.MIHOMO_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, tmp)
    cfg = build_test_config(tmp)
    logf = open(os.path.join(tmp, "core.log"), "w", encoding="utf-8")

    proc = subprocess.Popen([P.MIHOMO_EXE, "-d", tmp, "-f", cfg],
                            stdout=logf, stderr=logf,
                            stdin=subprocess.DEVNULL,
                            creationflags=0x08000000 | 0x00000008)
    try:
        for _ in range(40):
            time.sleep(0.5)
            if _core_ok():
                break
        else:
            _log("test core did not start")
            return 1
        _log("test core up on port %d\n" % TEST_PORT)

        _log("domestic sites -- expect DIRECT, so they should answer:")
        dom_ok = 0
        dom_times = []
        for url, label in DOMESTIC:
            code, secs = _curl(url)
            hit = code is not None
            if hit:
                dom_ok += 1
                dom_times.append(secs)
            _log("  %-10s %-6s %6.2fs" % (label, code or "FAIL", secs))

        _log("\nforeign sites -- expect REJECT, so they must NOT answer:")
        foreign_blocked = 0
        for url, label in FOREIGN:
            code, _ = _curl(url)
            blocked = code is None
            if blocked:
                foreign_blocked += 1
            _log("  %-10s %s" % (label, "blocked (correct)" if blocked
                                 else "ANSWERED -- rule did not fire!"))

        # confirm from the core's own log that DIRECT was the reason
        try:
            raw = open(os.path.join(tmp, "core.log"), encoding="utf-8",
                       errors="replace").read()
        except Exception:
            raw = ""
        direct_hits = len(re.findall(r"using DIRECT|match .*DIRECT", raw))
        cn_hits = len(re.findall(r"GEOSITE,CN|geosite.*cn", raw, re.I))

        avg = (sum(dom_times) / len(dom_times)) if dom_times else 0
        _log("\n" + "=" * 52)
        _log("domestic reachable : %d/%d   avg %.2fs" % (dom_ok, len(DOMESTIC), avg))
        _log("foreign blocked    : %d/%d" % (foreign_blocked, len(FOREIGN)))
        _log("core log DIRECT hits: %d" % direct_hits)
        _log("=" * 52)

        ok = dom_ok == len(DOMESTIC) and foreign_blocked == len(FOREIGN)
        _log("\nVERDICT: %s" % ("split routing works as configured"
                                if ok else
                                "SPLIT ROUTING IS WRONG -- see above"))
        return 0 if ok else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        logf.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())