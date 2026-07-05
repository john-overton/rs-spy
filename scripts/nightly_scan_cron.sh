#!/bin/sh
# Cron wrapper for the M9 nightly universe scan (see src/rs_spy/scan/nightly.py).
# Installed as: 0 16 * * 1-5  (16:00 America/Chicago == 17:00 ET, weekdays)
# cron provides a bare environment and runs from $HOME, so everything here is
# absolute: cd into the repo (Settings loads .env relative to cwd), venv python.
REPO=/Users/johnoverton/Development/rs-spy
# Operator pause switch: `touch $REPO/.nightly_paused` skips the run (used
# during long bulk backfills that hold the main warehouse's write lock);
# remove the file to resume. Avoids editing the crontab itself.
if [ -f "$REPO/.nightly_paused" ]; then
    echo "$(date): .nightly_paused present -- skipping nightly scan" >> "$REPO/logs/nightly_scan.log"
    exit 0
fi
cd "$REPO" || exit 1
exec "$REPO/.venv/bin/python" "$REPO/scripts/run_nightly_scan.py" >> "$REPO/logs/nightly_scan.log" 2>&1
