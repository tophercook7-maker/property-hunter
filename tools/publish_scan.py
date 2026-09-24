"""Publish scan results to the site: rebuild the exports, commit the data files, push.

    python3 tools/publish_scan.py            # once
    python3 tools/publish_scan.py --loop 90  # every 90 minutes while the statewide hunt runs
"""
import os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


STATE_LANDS = os.path.join(ROOT, "docs", "data", "state_lands.json")
REFRESH_AFTER_H = 20


def refresh_state_lands_if_stale():
    """The GitHub Action that refreshed State Lands has failed since 2026-09-20 (COSL does not answer the runner);
    the Mac can read COSL, so the loop refreshes once a day when the file is older than REFRESH_AFTER_H hours."""
    try:
        age_h = (time.time() - os.path.getmtime(STATE_LANDS)) / 3600 if os.path.exists(STATE_LANDS) else 1e9
    except OSError:
        age_h = 1e9
    if age_h < REFRESH_AFTER_H:
        return
    print(time.strftime("%H:%M"), f"state_lands.json is {age_h:.0f} h old; refreshing from COSL", flush=True)
    log = open(os.path.join(ROOT, "data", "state_lands_refresh.log"), "a")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_state_lands.py"), "--details"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False, timeout=3600)
    print(time.strftime("%H:%M"), "state lands refresh", "ok" if r.returncode == 0 else f"failed ({r.returncode}); see data/state_lands_refresh.log", flush=True)


def once():
    try:
        refresh_state_lands_if_stale()
    except Exception as exc:                                             # never let a refresh problem stop publishing
        print(time.strftime("%H:%M"), "state lands refresh error:", exc, flush=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_share.py")], cwd=ROOT, stdout=subprocess.DEVNULL, check=False)
    subprocess.run(["git", "add", "docs/data/scan", "docs/data/scan_index.json", "docs/data/garland.json", "docs/data/status.json", "docs/data/hunt_status.json", "docs/data/changes.json", "docs/data/counties.json", "docs/data/signals.json", "docs/data/radar.json", "docs/data/tax_sources.json", "docs/data/timeline", "docs/data/state_lands.json", "docs/data/history", "docs/garland.html", "hunter/static/share.html"], cwd=ROOT, check=False)
    r = subprocess.run(["git", "commit", "-qm", "Scan results refresh"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(time.strftime("%H:%M"), "nothing new to publish", flush=True); return
    subprocess.run(["git", "pull", "--rebase", "--autostash", "-q", "origin", "main"], cwd=ROOT, check=False)
    p = subprocess.run(["git", "push", "-q", "origin", "main"], cwd=ROOT, capture_output=True, text=True)
    print(time.strftime("%H:%M"), "published" if p.returncode == 0 else "push failed: " + p.stderr[:200], flush=True)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        mins = int(sys.argv[sys.argv.index("--loop") + 1])
        while True:
            once(); time.sleep(mins * 60)
    else:
        once()
