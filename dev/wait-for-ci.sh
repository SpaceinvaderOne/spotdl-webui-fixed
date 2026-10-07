#!/bin/bash
# Wait for a specific commit to land on GitHub, then watch the Actions run that
# the push triggers and report its verdict. Polls git (unlimited) for the push
# and the API (60 req/hr unauthenticated) only afterwards.
set -u
REPO=SpaceinvaderOne/spotdl-webui-fixed
LOCAL=${1:?usage: wait-for-ci.sh <full-sha>}

echo "watching for $LOCAL to appear on $REPO main..."
remote=""
for _ in $(seq 1 240); do
  remote=$(git ls-remote "https://github.com/$REPO" main 2>/dev/null | awk '{print $1}')
  [ "$remote" = "$LOCAL" ] && break
  sleep 15
done
if [ "$remote" != "$LOCAL" ]; then
  echo "RESULT: no push seen after 60 minutes (remote still ${remote:-unreachable})"
  exit 0
fi
echo "push seen at $(date -u +%H:%M:%S)Z - waiting for the build to finish"

for _ in $(seq 1 40); do
  sleep 60
  line=$(curl -s --max-time 20 "https://api.github.com/repos/$REPO/actions/runs?per_page=5" |
    python3 -c '
import json, sys
try:
    runs = json.load(sys.stdin).get("workflow_runs", [])
except Exception:
    sys.exit(0)
for r in runs:
    if r["head_sha"].startswith("'"${LOCAL:0:7}"'"):
        print(r["status"], r["conclusion"], r["id"])
        break
')
  [ -n "$line" ] || continue
  echo "$(date -u +%H:%M:%S)Z  $line"
  case "$line" in
    completed*)
      echo "RESULT: $line"
      echo "run: https://github.com/$REPO/actions/runs/${line##* }"
      exit 0
      ;;
  esac
done
echo "RESULT: run still not finished after 40 minutes of watching"
