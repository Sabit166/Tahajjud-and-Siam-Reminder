#!/usr/bin/env bash
# Safely pull the latest commit and redeploy the bot without touching
# persistent data in ./data. Intended to be run from cron.
#
# Usage: ./deploy.sh   (run from inside the project directory)

set -euo pipefail

cd "$(dirname "$0")"

LOG_FILE="deploy.log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') : Starting deploy =====" >> "$LOG_FILE"

# Record current commit so we only rebuild if something actually changed
BEFORE=$(git rev-parse HEAD)

git fetch origin >> "$LOG_FILE" 2>&1
git reset --hard origin/main >> "$LOG_FILE" 2>&1   # change 'main' if your branch differs

AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "No new commits. Skipping rebuild." >> "$LOG_FILE"
    exit 0
fi

echo "New commit detected ($BEFORE -> $AFTER). Rebuilding..." >> "$LOG_FILE"

# ./data is a bind mount defined in docker-compose.yml and lives outside
# the git repo / image, so it is never touched by pull or rebuild.
docker compose up -d --build >> "$LOG_FILE" 2>&1

echo "Deploy finished at $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
