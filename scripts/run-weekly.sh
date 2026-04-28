#!/bin/bash
# Runs the weekly DEI trend report. Invoked by launchd Mondays at 08:30.
# (Scheduled 30 minutes after the daily run so today's items are already in DB.)
# Logs go to logs/weekly-YYYY-MM-DD.log
set -euo pipefail

PROJECT="/Users/marciachen/dei-research-assistant"
cd "$PROJECT"

# shellcheck disable=SC1091
source "$PROJECT/.venv/bin/activate"

LOG_DIR="$PROJECT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/weekly-$(date +%Y-%m-%d).log"

echo "===== Started: $(date) =====" >> "$LOG_FILE"
python main.py weekly >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

# Auto-publish to gh-pages if a remote is set up. Failures here don't
# fail the weekly job (e.g. before first-time GitHub setup).
echo "----- Publishing to gh-pages -----" >> "$LOG_FILE"
bash "$PROJECT/scripts/publish.sh" >> "$LOG_FILE" 2>&1 || \
    echo "publish.sh skipped (no remote / first-time setup pending)" >> "$LOG_FILE"

echo "===== Finished: $(date) (exit=$EXIT_CODE) =====" >> "$LOG_FILE"
exit $EXIT_CODE
