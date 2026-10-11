"""
auto_tune.py — pick the right node without being told.

The point of the purpose groups (WEB / DOWN / MEDIA) was that "fastest" is
not one number. But making the user pick defeats the purpose: nobody knows
which bucket they need before they need it.

So we infer it. The signals, in order of reliability:

  1. What is being downloaded. A git-lfs pointer or a release asset is a
     multi-hundred-megabyte transfer; a page load is not. The core already
     routes those hosts to the DOWN group on its own (see the rule set), so
     this is partly handled before we start.

  2. How much is moving right now. Sustained downstream traffic means the
     user is downloading, which means throughput matters more than latency.
     A single request is the opposite.

  3. How long the current node has been slow. If the live throughput has
     been poor for a while, rotation beats waiting for url-test's interval.

The result is advisory: it recommends a group, and the tray applies it. It
never fights the user -- an explicit choice pins for a while.
"""
import os
import sys
import time
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pipeline import paths as P  # noqa: E402

# A request larger than this counts as "download".
PAGE_BYTES = 120_000        # about one web page
STREAM_BYTES = 2_000_000    # audio/video
SUSTAIN_SECONDS = 12        # how long it must hold before we call it a download

STATE = os.path.join(P.CONFIG, "auto_tune.json")

# How long a manual choice is honoured before automation takes over again.
MANUAL_PIN_SECONDS = 900   # 15 minutes


def _load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(d):
    try:
        P.ensure_dirs()
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


def current_group():
    try:
        import urllib.request
        d = json.loads(urllib.request.urlopen(
            P.API + "/proxies/PROXY", timeout=4).read().decode("utf-8", "replace"))
        return d.get("now") or ""
    except Exception:
        return ""


def pick(up, down, now=None):
    """
    Decide which group should handle the traffic right now.
    Returns (group, reason).
    """
    st = _load()
    now_ts = time.time()

    # a manual choice wins for a while
    pinned = st.get("manual")
    if pinned and now_ts - pinned.get("at", 0) < MANUAL_PIN_SECONDS:
        if now != pinned.get("applied"):
            return pinned["group"], "honouring your manual choice"
        return None, "manual choice active"

    # measure how long this direction has been sustained
    now_ts = time.time()
    if down > STREAM_BYTES:
        st["down_since"] = st.get("down_since") or now_ts
    else:
        st["down_since"] = 0
    if up > STREAM_BYTES:
        st["up_since"] = st.get("up_since") or now_ts
    else:
        st["up_since"] = 0
    _save(st)

    # A stream this large is unambiguous whatever its duration. Checking it
    # before the "sustained" branch is what makes a 5 MB/s download land on
    # WEB: the sustain timer has not elapsed yet on the first sample.
    if down > STREAM_BYTES:
        held = now_ts - st["down_since"]
        if held >= SUSTAIN_SECONDS or st["down_since"] == now_ts:
            return "DOWN", "bulk download %.1f MB/s" % (down / 1048576.0)
    if up > STREAM_BYTES:
        held = now_ts - st["up_since"]
        if held >= SUSTAIN_SECONDS or st["up_since"] == now_ts:
            return "DOWN", "bulk upload %.1f MB/s" % (up / 1048576.0)

    # a one-shot burst: a page, an API call
    if down > PAGE_BYTES:
        _save(st)
        return "WEB", "single request, favouring latency"

    if now in ("DOWN", "MEDIA") and down <= PAGE_BYTES:
        # no longer downloading -> back to the general group
        return "FAST", "traffic is light again"

    return None, "current choice is right"


def note_manual(group):
    """Called when the user picks a group themselves."""
    st = _load()
    st["manual"] = {"group": group, "at": time.time()}
    _save(st)
    return st["manual"]


def clear_manual():
    st = _load()
    st.pop("manual", None)
    _save(st)