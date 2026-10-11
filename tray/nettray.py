"""
NetGuard — tray status light and control panel.

Design intent
-------------
  * At-a-glance health. No main window, ever.
  * Right-click is the whole UI. Grouped, ~25 items.
  * The tray icon itself shows live up/down speed.
  * Boot autostart toggle lives in the menu.

Portability
-----------
The tray works in two modes and behaves identically in both:

  source checkout   python tray/nettray.py
  frozen EXE        NetGuard.exe

When frozen, the pipeline and the intel layer are already inside the binary,
so long jobs run as threads instead of child processes, and all writable
state goes to %LOCALAPPDATA%\\NetGuard rather than next to the exe. That is
what lets someone copy the single file to any machine and just run it.
"""
import os
import sys
import json
import time
import threading
import subprocess
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import paths as P  # noqa: E402

# Make the bundled intel/ package importable when frozen.
if P.FROZEN and P.BUNDLE not in sys.path:
    sys.path.insert(0, P.BUNDLE)

APP_NAME = "NetGuard"

ROOT = P.ROOT
HOME = P.HOME
MIHOMO_DIR = P.MIHOMO_DIR
LOG_DIR = P.LOGS
REPORTS = P.REPORTS
STATE_FILE = P.TRAY_STATE
ALERT_FILE = P.ALERT_STATE
TASK_NAME = P.TRAY_TASK

API = P.API
PROXY_ADDR = P.PROXY_ADDR
HIDE = 0x08000000
DETACHED = 0x00000008

_state = {"color": "grey", "msg": "启动中", "node": "", "up": 0, "down": 0,
          "alive": 0, "fast": 0, "busy": False, "autostart": False}


# ---------------------------------------------------------------- icon drawing
#
# Windows has no text area next to a tray icon. There is no API for it at
# all -- the area to the right of a tray icon belongs to Explorer and shows
# other applications' icons. So the rate has to go into the bitmap, and the
# bitmap is rendered at 16x16 on a standard-DPI desktop.
#
# Three layouts were tried against an actual 16px render:
#   64px with two rates -> unreadable smear
#   32px with two rates -> the dot squashed into an ellipse
#   32px with a dot plus one number -> the number is legible, the dot survives
#
# So: one number, the download rate, because that is what you glance at. The
# upload rate and everything else lives in the tooltip on hover, which is
# what every Windows tray app does.
#
# Colour carries the state; the number carries the throughput.

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL = True
except Exception:
    _PIL = False

_FONT = None


def _font(sz=13):
    global _FONT
    if _FONT is None:
        for name in ("segoeuib.ttf", "arialbd.ttf", "tahomabd.ttf"):
            try:
                _FONT = ImageFont.truetype(name, sz)
                break
            except Exception:
                continue
        else:
            _FONT = ImageFont.load_default()
    return _FONT


def _fmt_rate(bps, compact=True):
    """
    compact=True  -> three glyphs or fewer, for the 16px icon
    compact=False -> full precision, for the menu and the tooltip
    """
    if bps <= 0:
        return "0"
    if bps >= 1048576:
        v = bps / 1048576.0
        if not compact:
            return "%.2f MB/s" % v
        return ("%.1fM" % v) if v < 10 else ("%dM" % round(v))
    v = bps / 1024.0
    if not compact:
        return "%.0f KB/s" % v
    return ("%dK" % v) if v < 1000 else "1M"


def _icon(color, ring=False, inner=None, rate=None, dimmed=False, show_rate=True):
    """
    32x32 source, which Windows shows as 16x16 or 32x32 depending on DPI.

    Layout:
        the number, filling most of the box, in the state colour
        a small dot in the corner so the state still reads when the number
        is unreadable (idle, or a rate too small to show)
    """
    if not _PIL:
        return None
    size = 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    c = color
    if dimmed:
        c = (c[0] // 2 + 60, c[1] // 2 + 60, c[2] // 2 + 60, 255)

    down = rate[1] if rate else 0
    txt = _fmt_rate(down) if (rate and show_rate) else ""

    if txt:
        # the whole icon is the number; colour is the state
        f = _font(20 if len(txt) > 2 else 23)
        try:
            w = d.textlength(txt, font=f)
        except Exception:
            w = len(txt) * 13
        x = (size - w) / 2.0
        # heavy dark outline: survives both light and dark taskbars
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx or dy:
                    d.text((x + dx, 3 + dy), txt, fill=(28, 28, 28, 255), font=f)
        d.text((x, 3), txt, fill=c, font=f)
    else:
        # idle: a plain dot, same as before
        if ring:
            d.ellipse([1, 1, 30, 30], outline=c, width=3)
            d.ellipse([11, 11, 20, 20], fill=c)
        else:
            d.ellipse([5, 5, 26, 26], fill=c)
    return img


COLORS = {
    "green": (58, 168, 96, 255),
    "amber": (214, 158, 32, 255),
    "red":   (208, 72, 66, 255),
    "grey":  (128, 128, 128, 255),
}

_icon_cache = {}


def get_icon(state, rate=None, dimmed=False):
    key = (state, rate, dimmed)
    if key not in _icon_cache:
        if len(_icon_cache) > 40:
            _icon_cache.clear()
        if state == "amber":
            im = _icon(COLORS[state], ring=True, inner=(255, 255, 255, 190),
                       rate=rate, dimmed=dimmed)
        elif state == "grey":
            im = _icon(COLORS[state], ring=True, rate=rate, dimmed=dimmed)
        else:
            im = _icon(COLORS[state], rate=rate, dimmed=dimmed)
        _icon_cache[key] = im
    return _icon_cache[key]


# ---------------------------------------------------------------- probing

def _http(url, timeout=3):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def core_alive():
    try:
        _http(API + "/version", 2)
        return True
    except Exception:
        return False


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def write_state(d):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def pipeline_running():
    """
    The pipeline holds a named mutex, but the tray cannot see that. It does
    write a lock file and a stage into tray_state.json, so combine both:
    a lock file younger than 30 minutes, or a stage that was updated recently.
    """
    try:
        lock = os.path.join(PROXY, "pipeline.lock")
        if os.path.exists(lock) and (time.time() - os.path.getmtime(lock)) < 1800:
            return True
    except Exception:
        pass
    st = read_state()
    if st.get("stage") in ("harvesting", "waiting"):
        ts = str(st.get("ts") or "")
        try:
            return (time.time() - os.path.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))) < 1800
        except Exception:
            return False
    return False


def proxy_current_node():
    try:
        now = json.loads(_http(API + "/proxies/PROXY", 3).decode("utf-8", "replace")).get("now") or ""
        for _ in range(3):
            if now in ("FAST", "AUTO", "FALLBACK", "PROXY", "MANUAL"):
                sub = json.loads(_http(API + "/proxies/" + now, 3).decode("utf-8", "replace"))
                nxt = sub.get("now") or ""
                if not nxt or nxt == now:
                    break
                now = nxt
            else:
                break
        return now
    except Exception:
        return ""


def traffic():
    """one sample of (up, down) bytes/sec from the core"""
    try:
        with urllib.request.urlopen(API + "/traffic", timeout=4) as r:
            line = r.readline().decode("utf-8", "replace")
        d = json.loads(line)
        return int(d.get("up", 0)), int(d.get("down", 0))
    except Exception:
        return 0, 0


def wall_reachable():
    """
    Is the far side actually reachable through the proxy?

    The timeout is generous on purpose. Measured on a real run: Google
    answered in 7.6 seconds through a free node. An 8-second budget sits
    right on that boundary, so a merely-slow node got reported as "no
    internet". Free nodes are routinely slow; the check has to tolerate that
    or the light lies.

    Anything beyond this is genuinely broken.
    """
    for url in ("https://www.google.com/generate_204",
                "https://www.gstatic.com/generate_204"):
        try:
            r = subprocess.run(["curl.exe", "-s", "-x", "http://" + PROXY_ADDR,
                                "-o", "NUL", "-m", "20", "-w", "%{http_code}", url],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=26, creationflags=HIDE)
            if r.stdout.strip() in ("200", "204"):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------- alerts

def _alerts():
    try:
        with open(ALERT_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"bad_since": 0}


def _save_alerts(d):
    try:
        with open(ALERT_FILE, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
    except Exception:
        pass


def check_alert(color, msg):
    """
    Notify only on the transition into a bad state, then stay quiet for
    30 minutes. Colour changes alone are too easy to miss.
    """
    a = _alerts()
    now = time.time()
    bad = color == "red"
    if bad:
        if not a.get("bad_since"):
            a["bad_since"] = now
            a["notified"] = 0
        elif not a.get("notified") and now - a["bad_since"] > 60:
            a["notified"] = now
            _save_alerts(a)
            return True, "代理异常已持续 1 分钟：%s\n右键 → 立即重新筛选" % msg
    else:
        if a.get("bad_since"):
            a["bad_since"] = 0
            a["notified"] = 0
            a["recovered"] = now
    _save_alerts(a)
    return False, ""


def _notify(icon, msg, title=APP_NAME):
    try:
        icon.notify(msg, title)
    except Exception:
        pass


# ---------------------------------------------------------------- actions

def open_log(icon, item):
    p = P.PIPELINE_LOG
    if os.path.exists(p):
        subprocess.Popen(["notepad.exe", p], creationflags=HIDE)
    else:
        os.startfile(P.LOGS)


def open_custom_subs(icon, item):
    """
    Open the user's subscription file in Notepad, creating it if needed.

    This is the one lever that changes the outcome. Measured: 1436 nodes from
    the most popular free projects produced zero usable ones, so the program
    cannot be improved by better filtering -- only by a better source. A menu
    entry that goes straight to the file is the shortest path between the user
    and the thing that actually helps, which is why it sits at the top level
    rather than buried under a diagnostics submenu.
    """
    try:
        P.ensure_dirs()
        if not os.path.exists(P.CUSTOM_SUBS):
            P.seed()
        subprocess.Popen(["notepad.exe", P.CUSTOM_SUBS], creationflags=HIDE)
    except Exception as e:
        _notify(icon, "无法打开订阅文件：%s" % e)


def show_custom_status(icon, item):
    """Tell the user how many of their own subscriptions are configured."""
    try:
        info = P.custom_subs_help()
        n = info["count"]
        if n:
            _notify(icon, "已配置 %d 个自己的订阅源" % n)
        else:
            _notify(icon, "还没有配置自己的订阅源 —— 点上面那项打开文件填写")
    except Exception as e:
        _notify(icon, "读取失败：%s" % e)


def _bg(fn, *args):
    """
    Run something slow in the background.

    When frozen, the modules live inside this very binary, so we cannot
    relaunch "python intel/x.py" -- we just run it in a thread. When running
    from a checkout we spawn the interpreter so a crash cannot take the tray
    down with it.
    """
    if _paths.FROZEN:
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()
        return True
    return False


def _spawn_module(mod_name, *mod_args):
    """
    Execute an intel/ module. Frozen: import and call in a thread.
    Source: launch the interpreter in a detached process.
    """
    def _inproc():
        try:
            mod = __import__("intel." + mod_name, fromlist=["*"])
            if hasattr(mod, "run"):
                mod.run()
            elif hasattr(mod, "main"):
                mod.main()
        except Exception as e:
            _trace("bg %s failed: %s" % (mod_name, e))

    if _bg(_inproc):
        return True
    entry = {
        "tg_harvest": "tg_harvest.py",
        "manage_sources": "manage_sources.py",
        "report": "report.py",
        "bootstrap": "bootstrap.py",
    }.get(mod_name, mod_name + ".py")
    script = os.path.join(P.INTEL, entry)
    if not os.path.exists(script):
        script = os.path.join(P.RES, "intel", entry)
    subprocess.Popen([_paths.python_exe() or "python", script] + list(mod_args),
                     creationflags=HIDE | DETACHED,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return False


def _trace(msg):
    try:
        P.ensure_dirs()
        with open(os.path.join(P.LOGS, "tray.log"), "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _run_pipeline(icon, skip_idle=True, label="立即筛选"):
    _notify(icon, label + "已开始，后台运行（约 8-10 分钟）")

    def _work():
        try:
            os.environ["NETGUARD_SKIP_IDLE"] = "1" if skip_idle else "0"
            from intel import pipeline as pl
            pl.run(skip_sources=False, skip_idle=skip_idle)
        except Exception as e:
            _trace("pipeline failed: %s" % e)

    if _bg(_work):
        return
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File",
            os.path.join(P.PIPELINE, "pipeline.ps1")]
    if skip_idle:
        args.append("-SkipIdleCheck")
    subprocess.Popen(args, creationflags=HIDE | DETACHED)


def run_now(icon, item):
    _run_pipeline(icon, skip_idle=True, label="立即重新筛选")


def first_run_bootstrap(icon, item):
    _notify(icon, "正在下载代理内核（约 90 MB，只需一次）…")

    def _work():
        try:
            from intel import bootstrap
            bootstrap.run()
        except Exception as e:
            _trace("bootstrap failed: %s" % e)

    if _bg(_work):
        return
    _spawn_module("bootstrap")


def discover_new_sources(icon, item):
    _notify(icon, "全网搜索新源已开始，完成后自动入库（非转载才留）")
    script = os.path.join(P.PIPELINE, "discover.ps1")
    if _paths.FROZEN or not os.path.exists(script):
        _notify(icon, "该功能在 EXE 模式下请用命令行运行 discover.ps1")
        return
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-WindowStyle", "Hidden", "-File", script],
                     creationflags=HIDE | DETACHED)


def _show_report(name, icon):
    def _work():
        try:
            from intel import report
            report.main()
        except Exception as e:
            _trace("report failed: %s" % e)

    if not _bg(_work):
        subprocess.Popen([_paths.python_exe() or "python",
                          os.path.join(P.INTEL, "report.py")],
                         creationflags=HIDE | DETACHED,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = os.path.join(P.REPORTS, name)
    for _ in range(30):
        if os.path.exists(p) and os.path.getmtime(p) > time.time() - 30:
            break
        time.sleep(0.4)
    if os.path.exists(p):
        subprocess.Popen(["notepad.exe", p], creationflags=HIDE)
    else:
        _notify(icon, "报告尚未生成")


def show_overview(icon, item):
    _show_report("overview.txt", icon)


def start_core(icon, item):
    """Launch the mihomo core detached, hidden."""
    if not os.path.exists(P.MIHOMO_EXE):
        _notify(icon, "内核未下载，正在准备…")
        first_run_bootstrap(icon, item)
        return
    cfg = P.CONFIG_YAML
    if not os.path.exists(cfg):
        _notify(icon, "尚无配置，请先右键运行一次筛选")
        return
    os.makedirs(P.LOGS, exist_ok=True)
    logf = open(P.MIHOMO_LOG, "a", encoding="utf-8", errors="replace")
    subprocess.Popen([P.MIHOMO_EXE, "-d", P.MIHOMO_DIR, "-f", cfg],
                     creationflags=HIDE | DETACHED,
                     stdout=logf, stderr=logf, stdin=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(0.5)
        if core_alive():
            _notify(icon, "内核已启动")
            return
    _notify(icon, "内核启动超时，看 logs\\mihomo.log")


def restart_core(icon, item):
    _notify(icon, "正在重启代理内核")
    script = os.path.join(P.PIPELINE, "core-ctl.ps1")
    if not _paths.FROZEN and os.path.exists(script):
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", script, "-Restart"],
                         creationflags=HIDE | DETACHED)
        return
    # in-process: kill, wait, relaunch
    subprocess.run(["taskkill", "/F", "/IM", "mihomo.exe"],
                   capture_output=True, creationflags=HIDE)
    time.sleep(2)
    start_core(icon, item)


def close_connections(icon, item):
    try:
        req = urllib.request.Request(API + "/connections", method="DELETE")
        urllib.request.urlopen(req, timeout=4)
        _notify(icon, "已关闭全部连接")
    except Exception:
        _notify(icon, "操作失败")


def set_system_proxy(icon, item):
    """Toggle the Windows system proxy setting."""
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
            enabled = winreg.QueryValueEx(k, "ProxyEnable")[0]
            if enabled:
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                _notify(icon, "已关闭系统代理")
            else:
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ,
                                  "127.0.0.1:7890")
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                                  "localhost;127.*;<local>")
                winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                _notify(icon, "系统代理已开启：127.0.0.1:7890")
    except Exception as e:
        _notify(icon, "无法修改系统代理：%s" % e)


def system_proxy_enabled():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return bool(winreg.QueryValueEx(k, "ProxyEnable")[0])
    except Exception:
        return False


def _select_group(name):
    body = json.dumps({"name": name}).encode("utf-8")
    req = urllib.request.Request(API + "/proxies/PROXY", data=body, method="PUT",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5)


def toggle_autostart(icon, item):
    exe = sys.executable
    if getattr(sys, "frozen", False):
        cmd = '"%s"' % exe
    else:
        cmd = '"%s" "%s"' % (exe, os.path.abspath(__file__))
    if autostart_enabled():
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                       capture_output=True, creationflags=HIDE)
        _notify(icon, "已关闭开机自启")
    else:
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME,
                            "/TR", cmd, "/SC", "ONLOGON", "/F", "/RL", "HIGHEST"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=HIDE)
        _notify(icon, "已开启开机自启" if r.returncode == 0
                else "注册失败：需以管理员身份运行")


def autostart_enabled():
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, creationflags=HIDE)
        return r.returncode == 0
    except Exception:
        return False


def about(icon, item):
    mode = "单文件 EXE" if P.FROZEN else "源码模式"
    _notify(icon,
            "NetGuard 2.1\n"
            "托盘状态灯 · 自动扒源 · 自动测速 · 自我进化\n"
            "运行模式：%s\n"
            "内核: mihomo   测速: clash-speedtest\n"
            "数据目录: %s" % (mode, HOME), "关于")


def quit_app(icon, item):
    icon.stop()


# ---------------------------------------------------------------- menu

def _run_split_check(icon, item):
    _notify(icon, "正在自检分流：国内应直连、国外应走代理…")
    out = os.path.join(P.REPORTS, "split_check.txt")
    P.ensure_dirs()

    def _work():
        try:
            from intel import split_check
            import io
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                rc = split_check.main()
            finally:
                sys.stdout = old
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(buf.getvalue())
            _notify(icon, "分流自检完成，点击「分流自检结果」查看"
                    if rc == 0 else "分流自检发现问题，点击查看详情",
                    "NetGuard")
        except Exception as e:
            _trace("split_check failed: %s" % e)
            _notify(icon, "自检运行失败：%s" % e, "NetGuard")

    if _bg(_work):
        return
    subprocess.Popen([_paths.python_exe() or "python",
                      os.path.join(P.INTEL, "split_check.py")],
                     creationflags=HIDE | DETACHED,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _mk(p):
    """
    The menu is deliberately short.

    Everything that can be automated, is: the node group follows what you are
    actually doing (see intel/auto_tune.py), screening runs on a schedule and
    repairs itself, and diagnostics run on their own. What is left is the two
    switches people genuinely want to control, plus an escape hatch when
    something is wrong and you want to see why.

    Removed after feedback: a purpose-switcher (the automation does it), a
    folder browser (nobody used it), and separate manual screening actions
    (the schedule already covers it).
    """
    return p.Menu(
        # ---- live status
        p.MenuItem("状态：", lambda i: None, enabled=False),
        p.MenuItem("当前节点：", lambda i: None, enabled=False),
        p.MenuItem("实时速度：", lambda i: None, enabled=False),
        p.MenuItem("自动优化：", lambda i: None, enabled=False),
        p.Menu.SEPARATOR,

        # ---- the only two switches worth exposing
        p.MenuItem("系统代理", set_system_proxy,
                   checked=lambda i: system_proxy_enabled()),
        p.MenuItem("开机自启", toggle_autostart,
                   checked=lambda i: autostart_enabled()),
        p.Menu.SEPARATOR,

        # ---- the one thing that actually makes it fast
        p.MenuItem("我的订阅（填了才快）", open_custom_subs),
        p.Menu.SEPARATOR,

        # ---- when something is wrong and you need to see why
        p.MenuItem("状态详情", p.Menu(
            p.MenuItem("立即重新筛选", run_now),
            p.MenuItem("重启内核", restart_core),
            p.MenuItem("关闭所有连接", close_connections),
            p.Menu.SEPARATOR,
            p.MenuItem("我的订阅状态", show_custom_status),
            p.MenuItem("节点来源统计", show_overview),
            p.MenuItem("分流自检", _run_split_check),
            p.MenuItem("查看报告", show_overview),
            p.MenuItem("运行日志", open_log),
        )),
        p.MenuItem("退出", quit_app),
    )


def build_menu():
    import pystray

    class Dynamic(pystray.MenuItem):
        """Status line whose text is rebuilt from the cached state each open."""
        def __init__(self, getter):
            super().__init__(getter(), None)
            self._get = getter

        def __str__(self):
            return self._get()

    m = _mk(pystray)
    items = []
    it = iter(m)
    for _ in range(4):
        items.append(next(it))
    items[0] = Dynamic(lambda: "状态：%s" % _state["msg"])
    items[1] = Dynamic(lambda: "当前节点：%s" % (_state["node"] or "-"))
    items[2] = Dynamic(lambda: "实时速度：↑ %s  ↓ %s" %
                       (_fmt_rate(_state["up"], False),
                        _fmt_rate(_state["down"], False)))
    items[3] = Dynamic(lambda: "自动优化：%s" % _state.get("tune", "启用中"))
    items += list(it)
    return pystray.Menu(*items)


# ---------------------------------------------------------------- loop

_GROUP_LABEL = {
    "WEB": "网页优先（低延迟）",
    "DOWN": "下载优先（高带宽）",
    "MEDIA": "流媒体优先（稳）",
    "FAST": "通用优选",
    "AUTO": "全量自动",
    "FALLBACK": "兜底",
}


def auto_tune(up, dn):
    """
    Let the node choice follow what the user is actually doing. Returns a
    short description of what it decided, for the menu line.
    """
    now = proxy_current_node()
    try:
        from intel import auto_tune as at
        target, reason = at.pick(up, dn, now=now)
        if target:
            _select_group(target)
            return "%s —— %s" % (_GROUP_LABEL.get(target, target), reason)
        return _GROUP_LABEL.get(now, now or "-") + "（自动）"
    except Exception as e:
        _trace("auto_tune failed: %s" % e)
        return _GROUP_LABEL.get(now, now or "-")


def sample():
    st = read_state()
    busy = pipeline_running() or st.get("stage") in ("harvesting", "waiting")
    alive, fast = int(st.get("alive") or 0), int(st.get("fast") or 0)
    up, dn = traffic()

    if not _bootstrap_ready():
        return "grey", "首次运行 · 右键下载内核", up, dn
    if not core_alive():
        return "red", "核心未运行 —— 代理不可用"
    if busy:
        color = "amber"
        msg = st.get("msg") or "正在筛选测速中…"
        if alive:
            msg = "%s（可用 %d）" % (msg, alive)
    elif st.get("stage") == "done" and int(st.get("verify") or 0) == 0 and st.get("elapsed"):
        color, msg = "red", "上次筛选未通过验证"
    else:
        color, msg = ("green", "代理正常") if wall_reachable() else ("red", "无法连接外网")

    _state.update(color=color, msg=msg, node=proxy_current_node(), up=up,
                  down=dn, alive=alive, fast=fast, busy=busy)
    return color, msg, up, dn


def refresh_loop(icon):
    last_tune = 0
    last_doctor = 0
    while True:
        try:
            color, msg, up, dn = sample()
            _state["msg"] = msg
            # the throughput goes into the bitmap: Windows gives a tray icon
            # no text area, so there is nowhere else to put it
            icon.icon = get_icon(color, rate=(up, dn), dimmed=_state["busy"])
            # The icon can only carry the download rate (16px has room for
            # one number and nothing else), so the tooltip carries the rest.
            icon.title = ("%s\n%s\n↑ %s   ↓ %s\n%s" %
                          (msg, _state["node"] or "-",
                           _fmt_rate(up, False), _fmt_rate(dn, False),
                           _state.get("tune", "")))
            # re-tune the node group every 20s. Six seconds is the sample
            # interval but not the decision interval -- switching on one
            # large burst would thrash.
            if color == "green" and time.time() - last_tune > 20:
                last_tune = time.time()
                _state["tune"] = auto_tune(up, dn)
                icon.menu = build_menu()
            write_state({"state": color, "msg": msg, "node": _state["node"],
                         "up": up, "down": dn, "alive": _state["alive"],
                         "fast": _state["fast"],
                         "tune": _state.get("tune", ""),
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            fire, note = check_alert(color, msg)
            if fire:
                _notify(icon, note, "NetGuard 告警")
            # Housekeeping once an hour: keep the core current, keep a known
            # good snapshot, verify the health endpoint still answers. This is
            # what makes the thing survive years without a maintainer.
            if _bootstrap_ready() and time.time() - last_doctor > 3600:
                last_doctor = time.time()
                _bg(_daily_doctor)
        except Exception:
            pass
        time.sleep(6)


def _daily_doctor():
    """Background maintenance. Never raises: a failed check must not kill the tray."""
    try:
        from intel import doctor
        doctor.run()
    except Exception as e:
        _trace("doctor failed: %s" % e)


def _bootstrap_ready():
    return os.path.exists(P.MIHOMO_EXE) and os.path.exists(P.CST_EXE)


def main():
    import pystray
    P.ensure_dirs()
    P.seed()

    icon = pystray.Icon(APP_NAME, icon=get_icon("grey"), title="NetGuard 启动中…",
                        menu=build_menu())
    threading.Thread(target=refresh_loop, args=(icon,), daemon=True).start()

    # First run on this machine: no binaries yet. Kick the download off in the
    # background and let the user keep using the machine.
    if not _bootstrap_ready():
        threading.Thread(target=lambda: _notify(icon, welcome_text()),
                         daemon=True).start()
        def _boot():
            time.sleep(3)
            try:
                from intel import bootstrap
                bootstrap.run()
            except Exception as e:
                _trace("bootstrap failed: %s" % e)
        threading.Thread(target=_boot, daemon=True).start()
    else:
        # Make sure the core is up even if the user never rebooted.
        threading.Thread(target=lambda: (time.sleep(4), start_core(icon, None)),
                         daemon=True).start()

    icon.run()


def welcome_text():
    return ("首次运行：正在下载代理内核（约 90 MB），请保持网络畅通。\n"
            "完成后托盘会变绿，右键 →「立即重新筛选」。")


if __name__ == "__main__":
    main()