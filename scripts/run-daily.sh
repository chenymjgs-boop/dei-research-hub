#!/bin/bash
# Runs the daily DEI pipeline. Invoked by launchd at 8am daily.
# Logs go to logs/daily-YYYY-MM-DD.log
set -euo pipefail

PROJECT="/Users/marciachen/dei-research-assistant"
cd "$PROJECT"

# Activate the project venv
# shellcheck disable=SC1091
source "$PROJECT/.venv/bin/activate"

LOG_DIR="$PROJECT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily-$(date +%Y-%m-%d).log"

echo "===== Started: $(date) =====" >> "$LOG_FILE"
python main.py daily >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "===== Finished: $(date) (exit=$EXIT_CODE) =====" >> "$LOG_FILE"
exit $EXIT_CODE
