#!/usr/bin/env python3
"""
build-exe.py — produce NetGuard.exe (one file, no dependencies).

Why a Python script instead of a .ps1: PowerShell mangles arguments that
contain a colon followed by a path separator, which is exactly what
PyInstaller's --add-data needs. Doing the build from Python removes a whole
class of quoting problems.

What goes in:
    - the tray entry point
    - pipeline/ and intel/ as importable packages
    - three seed files (sources.json, config.template.yaml, tg_channels.txt)

What stays out:
    - mihomo and clash-speedtest binaries (~90 MB, and they would go stale).
      intel/bootstrap.py downloads them from GitHub Releases on first run,
      which also means every user gets the current version.

Usage:
    python build-exe.py
"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
# The exe lands right next to the source so it is the one file a user grabs.
DIST = HERE
BUILD = os.path.join(HERE, ".build")

HIDDEN = [
    "pystray._win32",
    "pipeline.paths",
    "intel.pipeline",
    "intel.bootstrap",
    "intel.source_intel",
    "intel.manage_sources",
    "intel.record_yield",
    "intel.extract_fast",
    "intel.speed_result",
    "intel.clean_fast",
    "intel.idle",
    "intel.report",
    "intel.tg_harvest",
    "intel.split_check",
    "intel.final_check",
    "intel.doctor",
    "intel.discover",
    "intel.auto_tune",
]

# Pulling these in would add hundreds of MB for no benefit.
EXCLUDE = ["matplotlib", "numpy", "pandas", "pytest", "IPython", "notebook",
           "setuptools", "pip", "unittest", "pydoc_data"]

DATA = [
    ("config/sources.json", "."),
    ("pipeline/config.template.yaml", "."),
    ("pipeline/tg_channels.txt", "."),
]


def main():
    missing = [s for s, _ in DATA if not os.path.exists(os.path.join(HERE, s))]
    if missing:
        print("missing seed files: %s" % missing)
        return 1

    # Only the build cache is cleared. Note what is NOT here: an earlier version
    # of this script also wiped DIST -- which is the repository root, because
    # the exe is meant to sit next to the source. One run of that would have
    # deleted the project it was building from. Clear the cache, and clear only
    # the cache.
    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconsole",
        "--name", "NetGuard",
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", BUILD,
        "--paths", HERE,
    ]
    # --add-data needs absolute sources: --paths changes the base directory
    # PyInstaller resolves relative paths against, and the spec then looks in
    # the wrong folder.
    for src, dest in DATA:
        full = os.path.join(HERE, src.replace("/", os.sep))
        cmd.append("--add-data=%s:%s" % (full, dest))
    for m in HIDDEN:
        cmd += ["--hidden-import", m]
    for m in EXCLUDE:
        cmd += ["--exclude-module", m]
    cmd.append(os.path.join("tray", "nettray.py"))

    print("python  :", sys.executable)
    print("root    :", HERE)
    print("data    :", ", ".join(s for s, _ in DATA))
    print("hidden  :", len(HIDDEN), "modules")
    print("")
    rc = subprocess.call(cmd, cwd=HERE)
    if rc != 0:
        print("\nbuild FAILED (exit %d)" % rc)
        return rc

    exe = os.path.join(DIST, "NetGuard.exe")
    if not os.path.exists(exe):
        print("\nbuild produced no exe")
        return 1
    mb = os.path.getsize(exe) / 1048576.0
    print("\nOK  %.1f MB  ->  %s" % (mb, exe))
    print("")
    print("Test it properly:")
    print("  1. copy NetGuard.exe into an empty folder on another machine")
    print("  2. run it there -- it must not need anything else")
    print("  3. first run downloads the core into %%LOCALAPPDATA%%\\NetGuard")
    return 0


if __name__ == "__main__":
    sys.exit(main())