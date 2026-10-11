"""
paths.py — single source of truth for every filesystem path.

Two modes, and this is the whole reason the project is portable:

**Run from a checkout** (``python tray/nettray.py``)
    ROOT is the repository folder. Everything lives inside it.

**Run from a frozen EXE** (``NetGuard.exe``)
    PyInstaller sets ``sys._MEIPASS`` to a temporary extraction folder, so the
    bundled code and data are readable there but NOT writable and NOT
    permanent. So:

      - bundled, read-only stuff (config template, the intel/ modules)
        is read from ``sys._MEIPASS``
      - mutable stuff (the source library, node pool, logs, downloaded
        binaries) goes to ``%LOCALAPPDATA%\\NetGuard``

    That means a user can drop the EXE anywhere -- including a USB stick or a
    locked-down machine -- and it still works, because all writable state
    lands in their user profile instead of next to the binary.

Override the data folder with the NETGUARD_HOME environment variable.
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)
BUNDLE = getattr(sys, "_MEIPASS", "")      # set by PyInstaller, else ""

# ------------------------------------------------------------------ root

def _find_root():
    env = os.environ.get("NETGUARD_ROOT")
    if env:
        return os.path.abspath(env)
    # <root>/pipeline/paths.py -> <root>
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.dirname(here)
    if os.path.isdir(os.path.join(cand, "config")):
        return cand
    return cand


def _find_home():
    """Where mutable state lives."""
    env = os.environ.get("NETGUARD_HOME")
    if env:
        return os.path.abspath(env)
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "NetGuard")
    return _find_root()


ROOT = _find_root()
HOME = _find_home()

# ---- bundled resources: readable in both modes
if BUNDLE:
    RES = BUNDLE
else:
    RES = ROOT

# ---- mutable state: always in HOME
CONFIG = os.path.join(HOME, "config")
REPORTS = os.path.join(HOME, "reports")
LOGS = os.path.join(HOME, "logs")
BIN = os.path.join(HOME, "bin")
TRASH = os.path.join(HOME, ".trash")

PIPELINE = os.path.join(RES, "pipeline")
TRAY = os.path.join(RES, "tray")
INTEL = os.path.join(RES, "intel")

# ---- data files
SOURCES_JSON = os.path.join(CONFIG, "sources.json")
SOURCE_STATS = os.path.join(CONFIG, "source_stats.json")
SOURCE_INDEX = os.path.join(CONFIG, "source_index.json")
NODE_REPUTATION = os.path.join(CONFIG, "node_reputation.json")
TRAY_STATE = os.path.join(CONFIG, "tray_state.json")
ALERT_STATE = os.path.join(CONFIG, "alert_state.json")
PIPELINE_LOCK = os.path.join(CONFIG, "pipeline.lock")

# The user's own subscriptions. This is the escape hatch from the free pool.
#
# Measured: 1436 nodes across the three most popular free projects produced zero
# nodes able to complete a download, so no amount of source-hunting fixes the
# quality problem -- the free pool is simply dead. The only thing that changes
# the outcome is a source the user controls: a paid airport or a self-hosted
# VPS. This file is where that goes.
#
# Plain text, one entry per line, so it can be edited in Notepad with no
# tooling: a bare URL, or "name = url" to pick the display name. Comments with #.
# Sources found here are never pruned and never auto-disabled.
CUSTOM_SUBS = os.path.join(CONFIG, "custom_subscriptions.txt")

# ---- core config. The template ships with the app; config.yaml is generated.
CONFIG_YAML = os.path.join(CONFIG, "config.yaml")
TEMPLATE_YAML = os.path.join(RES, "pipeline", "config.template.yaml")

# ---- binaries: downloaded on first run, never committed
MIHOMO_DIR = os.path.join(BIN, "mihomo")
MIHOMO_EXE = os.path.join(MIHOMO_DIR, "mihomo.exe")
CST_EXE = os.path.join(BIN, "clash-speedtest.exe")

# mihomo resolves a provider's relative `path:` against the directory given to
# its -d flag, NOT against the directory holding config.yaml. We start it with
# -d <BIN>/mihomo, so "./subs/x.yaml" means <BIN>/mihomo/subs/x.yaml -- and
# there is no such folder, so every provider fails to load with a bare
# "The system cannot find the file specified" and the AUTO group ends up
# holding nothing but the COMPATIBLE placeholder. The user then sees a green
# tray icon and no internet.
#
# The fix is to keep the subs folder inside the core's own directory, so the
# relative path in the config resolves exactly where the files are written.
CORE_SUBS = os.path.join(MIHOMO_DIR, "subs")


def provider_path(name):
    """Where a provider file must live for mihomo to find it.

    Returns a path relative to MIHOMO_DIR, which is what the config needs.
    """
    return "./subs/%s.yaml" % name


def provider_file(name):
    """The absolute location of that same file on disk."""
    return os.path.join(CORE_SUBS, "%s.yaml" % name)


# The one place every provider file is written. It lives inside the core's own
# directory because that is the only place a relative `path:` in the config
# resolves to. Every writer goes through SUBS, so this cannot drift.
SUBS = CORE_SUBS

# ---- seeds shipped inside the bundle so a fresh EXE has something to start from
SEED_SOURCES = os.path.join(RES, "config", "sources.json")
SEED_TEMPLATE = TEMPLATE_YAML
SEED_CHANNELS = os.path.join(RES, "pipeline", "tg_channels.txt")

# ---- logs
PIPELINE_LOG = os.path.join(LOGS, "pipeline.log")
DISCOVER_LOG = os.path.join(LOGS, "discover.log")
WATCHDOG_LOG = os.path.join(LOGS, "watchdog.log")
MIHOMO_LOG = os.path.join(LOGS, "mihomo.log")

# ---- runtime
API = "http://127.0.0.1:9090"
PROXY_ADDR = "127.0.0.1:7890"
TRAY_TASK = "NetGuard-Tray"

# ---- external sources for the downloader
MIHOMO_RELEASES = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
CST_RELEASES = "https://api.github.com/repos/faceair/clash-speedtest/releases/latest"
RULESETS = "https://github.com/MetaCubeX/meta-rules-dat/releases/latest/download"
GITHUB_API = "https://api.github.com"

# GitHub token is only used by discover. Environment variable, never a file.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Mirrors for GitHub release assets, tried in order.
MIRRORS = ["", "https://gh-proxy.com/", "https://ghfast.top/", "https://ghproxy.net/"]


# ------------------------------------------------------------------ helpers

def ensure_dirs():
    for d in (CONFIG, SUBS, REPORTS, LOGS, BIN, MIHOMO_DIR, CORE_SUBS):
        os.makedirs(d, exist_ok=True)


def seed():
    """
    Copy the bundled defaults into HOME the first time the app runs.
    A frozen EXE has no writable folder next to itself, so without this
    there would be no source library and no config template to build from.
    """
    ensure_dirs()
    if not os.path.exists(SOURCES_JSON) and os.path.exists(SEED_SOURCES):
        try:
            import shutil
            shutil.copy2(SEED_SOURCES, SOURCES_JSON)
        except Exception:
            pass
    # Create the custom-subscription file with its instructions in place. An
    # empty file the user has to discover is worse than one that explains
    # itself, and a missing file gives the impression the feature does not
    # exist -- which, for the one lever that actually changes node quality,
    # is the most expensive possible outcome.
    if not os.path.exists(CUSTOM_SUBS):
        try:
            with open(CUSTOM_SUBS, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(CUSTOM_SUBS_TEMPLATE)
        except Exception:
            pass
    return SOURCES_JSON


CUSTOM_SUBS_TEMPLATE = """\
# ===========================================================================
#  你自己的订阅地址 —— 想真正变快，只需要这一件事
# ===========================================================================
#
#  为什么需要这个文件：
#  免费公开节点实测 1436 个里能用的是 0 个（下载速度全部为 0）。
#  它们来自同一个正在死掉的池子，多找几个源没有用。
#  唯一能改变结果的是你自己控制的节点：付费机场，或自建 VPS。
#
#  怎么用（就三步）：
#    1. 把你订阅的 URL 粘到下面任意一行
#    2. 保存
#    3. 不用重启，下一轮自动筛选就会用上（托盘右键 → 状态详情 → 立即筛选）
#
#  格式（三选一，随便）：
#    https://example.com/api/v1/client/subscribe?token=abcdef
#    我的机场 = https://example.com/api/v1/client/subscribe?token=abcdef
#    # 以 # 开头是注释，不会生效
#
#  这里填的源永远不会被自动禁用、永远不会被去重逻辑删掉。
#
# ===========================================================================


"""


def custom_subs_help():
    """Human-readable status of the custom subscription file."""
    n = 0
    if os.path.exists(CUSTOM_SUBS):
        try:
            for line in open(CUSTOM_SUBS, encoding="utf-8", errors="replace"):
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                # "name = url" is a valid entry, so the URL is not necessarily
                # at the start of the line -- checking only the first characters
                # undercounted and made a working file look empty.
                candidate = line.split("=", 1)[-1].strip() if "=" in line else line
                if candidate.lower().startswith(("http://", "https://")):
                    n += 1
        except Exception:
            pass
    return {"path": CUSTOM_SUBS, "count": n}


def python_exe():
    """The interpreter for running intel/ scripts, or None when frozen.

    When frozen, the modules are already inside the binary, so nothing
    external needs to be launched -- see run_pipeline() in the tray.
    """
    if FROZEN:
        return None
    return sys.executable


def describe():
    return {
        "frozen": FROZEN,
        "root": ROOT,
        "home": HOME,
        "bundle": BUNDLE or "(none)",
    }


if __name__ == "__main__":
    ensure_dirs()
    seed()
    print("mode     :", "FROZEN (single EXE)" if FROZEN else "source checkout")
    print("root     :", ROOT)
    print("home     :", HOME)
    print("bundle   :", BUNDLE or "(none)")
    print("config   :", CONFIG)
    print("subs     :", SUBS)
    print("bin      :", BIN)
    print("logs     :", LOGS)
    print("reports  :", REPORTS)
    print("mihomo   :", MIHOMO_EXE, "(present)" if os.path.exists(MIHOMO_EXE) else "(missing)")
    print("speedtest:", CST_EXE, "(present)" if os.path.exists(CST_EXE) else "(missing)")