#!/bin/zsh
# Waits for the main statewide runner to exit, then re-hunts the counties that lost records to the cross-county merge bug.
cd ~/Projects/property-hunter
while pgrep -f "scan_statewide.py --enrich" >/dev/null; do sleep 60; done
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tools/scan_statewide.py --only "$(cat data/rehunt_queue.txt)" --enrich 5
