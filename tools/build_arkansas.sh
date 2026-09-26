#!/bin/bash
# Build all of Arkansas: fetch the roll, load it, score it, publish it.
# Resumable at every stage -- re-running picks up where it stopped.
set -u
cd "$(dirname "$0")/.." || exit 1
LOG=data/logs
mkdir -p "$LOG"
say(){ echo "[$(date '+%H:%M:%S')] $*"; }

say "waiting for the roll fetch"
while pgrep -f fetch_state_roll >/dev/null; do sleep 60; done
say "fetch done: $(ls data/roll/*.jsonl 2>/dev/null | wc -l | tr -d ' ') counties cached, $(du -sh data/roll | cut -f1)"

say "loading every cached county"
python3 tools/load_state_roll.py >> "$LOG/state_load.log" 2>&1
say "load exit $?"

say "scoring"
python3 tools/score_state.py >> "$LOG/state_score.log" 2>&1
say "score exit $?"

say "rebuilding published payloads"
python3 -c "import sys;sys.path.insert(0,'.');import tools.build_share as b;b.build()" >> "$LOG/state_publish.log" 2>&1
python3 tools/build_radar.py >> "$LOG/state_publish.log" 2>&1
python3 tools/weekly_brief.py >> "$LOG/state_publish.log" 2>&1
say "publish exit $?"

say "ALL OF ARKANSAS: done"
python3 tools/score_state.py --status 2>&1 | tail -5
df -h . | tail -1
