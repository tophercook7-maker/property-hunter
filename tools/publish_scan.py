"""Publish scan results to the site: rebuild the exports, commit the data files, push.

    python3 tools/publish_scan.py            # once
    python3 tools/publish_scan.py --loop 90  # every 90 minutes while the statewide hunt runs
"""
import os, subprocess, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def once():
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_share.py")], cwd=ROOT, stdout=subprocess.DEVNULL, check=False)
    subprocess.run(["git", "add", "docs/data/scan", "docs/data/scan_index.json", "docs/data/garland.json", "docs/data/status.json", "docs/data/hunt_status.json", "docs/data/changes.json", "docs/data/counties.json", "docs/garland.html", "hunter/static/share.html"], cwd=ROOT, check=False)
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
