"""
bootstrap.py — fetch the two binaries NetGuard drives.

This is what makes the EXE genuinely portable: it ships with no binaries
(they would add 90 MB and go stale), so on first run it pulls the core and
the speed test from the projects' official GitHub releases.

Mirrors are tried in order because a plain GitHub release download is
unreachable from mainland China. Everywhere else the first entry works.

Nothing here is specific to Windows paths; it only uses curl.exe (present on
Windows 10 1803+) and PowerShell-free zip extraction via Python's zipfile.
"""
import io
import os
import sys
import json
import time
import zipfile
import subprocess
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as P  # noqa: E402


def _log(msg):
    print("[bootstrap] %s" % msg, flush=True)


def _fetch(url, out, timeout=900):
    """Try each mirror until one produces a plausible file."""
    for m in P.MIRRORS:
        target = m + url
        try:
            _log("fetch %s" % (url.rsplit("/", 1)[-1][:52]))
            rc = subprocess.call(
                ["curl.exe", "-L", "-s", "-m", str(timeout),
                 "-o", out, target],
                creationflags=0x08000000)
            if rc == 0 and os.path.exists(out) and os.path.getsize(out) > 100_000:
                return True
        except Exception:
            continue
    return False


def _api(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NetGuard"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        _log("api lookup failed: %s" % e)
        return None


def _unzip(zip_path, dest):
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
        return True
    except Exception as e:
        _log("extract failed: %s" % e)
        return False


def mihomo():
    if os.path.exists(P.MIHOMO_EXE) and os.path.getsize(P.MIHOMO_EXE) > 5_000_000:
        _log("mihomo already installed")
        return True
    rel = _api(P.MIHOMO_RELEASES)
    if not rel or "assets" not in rel:
        _log("could not query the mihomo release feed")
        return False
    asset = None
    for a in rel["assets"]:
        n = a.get("name", "").lower()
        if "windows-amd64" in n and n.endswith(".zip") and "compatible" not in n:
            asset = a
            break
    if not asset:
        _log("no mihomo windows asset in %s" % rel.get("tag_name"))
        return False
    _log("mihomo %s (%.1f MB)" % (rel.get("tag_name"),
                                  asset["size"] / 1048576.0))
    os.makedirs(P.MIHOMO_DIR, exist_ok=True)
    zip_path = os.path.join(P.BIN, "_mihomo.zip")
    if not _fetch(asset["browser_download_url"], zip_path):
        return False
    if not _unzip(zip_path, P.MIHOMO_DIR):
        return False
    try:
        os.remove(zip_path)
    except Exception:
        pass
    got = [f for f in os.listdir(P.MIHOMO_DIR) if f.lower().endswith(".exe")]
    if got and got[0].lower() != "mihomo.exe":
        try:
            os.replace(os.path.join(P.MIHOMO_DIR, got[0]), P.MIHOMO_EXE)
        except Exception:
            pass
    return os.path.exists(P.MIHOMO_EXE)


def clash_speedtest():
    if os.path.exists(P.CST_EXE) and os.path.getsize(P.CST_EXE) > 2_000_000:
        _log("clash-speedtest already installed")
        return True
    rel = _api(P.CST_RELEASES)
    if not rel or "assets" not in rel:
        _log("could not query the clash-speedtest release feed")
        return False
    asset = None
    for a in rel["assets"]:
        n = a.get("name", "").lower()
        if "windows" in n and "x86_64" in n and n.endswith(".zip"):
            asset = a
            break
    if not asset:
        _log("no windows asset for clash-speedtest")
        return False
    _log("clash-speedtest %s (%.1f MB)" % (rel.get("tag_name"),
                                           asset["size"] / 1048576.0))
    os.makedirs(P.BIN, exist_ok=True)
    zip_path = os.path.join(P.BIN, "_cst.zip")
    tmp = os.path.join(P.BIN, "_cst_tmp")
    if not _fetch(asset["browser_download_url"], zip_path):
        return False
    if not _unzip(zip_path, tmp):
        return False
    for base, _, files in os.walk(tmp):
        for f in files:
            if f.lower() == "clash-speedtest.exe":
                import shutil
                shutil.copy2(os.path.join(base, f), P.CST_EXE)
                break
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.remove(zip_path)
    except Exception:
        pass
    return os.path.exists(P.CST_EXE)


def rulesets():
    """geoip.dat / geosite.dat drive the routing rules. Not fatal if absent."""
    os.makedirs(P.MIHOMO_DIR, exist_ok=True)
    ok = True
    for name in ("geoip.dat", "geosite.dat"):
        out = os.path.join(P.MIHOMO_DIR, name)
        if os.path.exists(out) and os.path.getsize(out) > 100_000:
            continue
        if _fetch(P.RULESETS + "/" + name, out, timeout=600):
            _log("%s ok (%.1f MB)" % (name, os.path.getsize(out) / 1048576.0))
        else:
            _log("%s FAILED - routing rules will not apply" % name)
            ok = False
    return ok


def update_mihomo(keep_backup=3):
    """
    Fetch the newest core, keeping the current one as a fallback.

    Used by doctor.update_core(). The old binary is copied aside first so a
    bad upgrade can be undone -- an update that leaves you with no proxy at
    all is worse than staying on an old version.
    """
    if not os.path.exists(P.MIHOMO_EXE):
        return mihomo()
    rel = _api(P.MIHOMO_RELEASES)
    if not rel or "assets" not in rel:
        return False
    asset = None
    for a in rel["assets"]:
        n = a.get("name", "").lower()
        if "windows-amd64" in n and n.endswith(".zip") and "compatible" not in n:
            asset = a
            break
    if not asset:
        return False

    # keep the previous binaries so a rollback is always possible
    try:
        prev_dir = os.path.join(P.HOME, "prev-core")
        os.makedirs(prev_dir, exist_ok=True)
        olds = sorted(
            (os.path.join(prev_dir, f) for f in os.listdir(prev_dir)),
            key=os.path.getmtime, reverse=True)
        for old in olds[keep_backup - 1:]:
            os.remove(old)
        import shutil
        shutil.copy2(P.MIHOMO_EXE,
                     os.path.join(prev_dir, "mihomo-%d.exe" % int(time.time())))
    except Exception:
        pass

    zip_path = os.path.join(P.BIN, "_mihomo_up.zip")
    tmp = os.path.join(P.BIN, "_mihomo_up")
    if not _fetch(asset["browser_download_url"], zip_path):
        return False
    if not _unzip(zip_path, tmp):
        return False
    got = None
    for base, _, files in os.walk(tmp):
        for f in files:
            if f.lower().endswith(".exe"):
                got = os.path.join(base, f)
                break
    if not got:
        return False
    import shutil
    try:
        shutil.copy2(got, P.MIHOMO_EXE)
    except Exception as e:
        _log("replace failed: %s" % e)
        return False
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.remove(zip_path)
    except Exception:
        pass
    _log("core updated from %s" % rel.get("tag_name"))
    return True


def all_done():
    return (os.path.exists(P.MIHOMO_EXE) and os.path.exists(P.CST_EXE))


def run(with_rulesets=True):
    """
    Returns True when the minimum is present. Rulesets are best-effort.
    """
    P.ensure_dirs()
    P.seed()
    t0 = time.time()
    _log("preparing in %s" % P.HOME)
    m1 = mihomo()
    m2 = clash_speedtest()
    if with_rulesets:
        rulesets()
    _log("done in %.0fs (core=%s speedtest=%s)" %
         (time.time() - t0, m1, m2))
    return m1 and m2


if __name__ == "__main__":
    ok = run(with_rulesets="--no-rulesets" not in sys.argv)
    sys.exit(0 if ok else 1)