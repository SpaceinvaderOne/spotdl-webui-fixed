#!/bin/bash
# Does a download survive the browser navigating away?  This is what actually
# happens when you paste a url into the search box:
#
#   1. /client/search yields SSE.redirect("/downloads")
#   2. the browser obeys, which ABORTS the in-flight /client/search request
#   3. uvicorn cancels the response task, and with it anything still awaited
#      inside the handler - which, upstream, is the entire download
#
# So this probe fires the request and then hangs up early, like a browser would,
# and then waits to see whether any file ever appears.
#
#   ./dev/abort-probe.sh <host-port> <spotify-url> [abort-after-seconds] [wait-seconds]
#
# Exit status: 0 if the download completed despite the abort, 1 if it did not.
port=${1:?usage: abort-probe.sh PORT "spotify url" [abort_after] [wait]}
url=${2:?missing url}
abort_after=${3:-2}
wait_for=${4:-180}
base=http://127.0.0.1:$port
music=${MUSIC_DIR:?set MUSIC_DIR to the host dir mounted at /music}
name=${CONTAINER:?set CONTAINER to the container name}

before=$(find "$music" -name '*.mp3' 2>/dev/null | wc -l)

# A real tab holds /client/load open; the download only runs for a registered
# client, and the downloads page reads that client's progress tracker.
# The file is unique per run: reading a previous run's file hands back a client
# id that no longer exists, and every request after it silently goes nowhere.
load=/tmp/abort-$port-$$.txt
rm -f "$load"
if ! curl -fsS -m 5 "$base/api/version" >/dev/null 2>&1; then
  echo "nothing answering on $base - the container is not up"
  exit 2
fi
curl -s -N --max-time $((wait_for + 120)) -H 'Datastar-Request: 1' -G "$base/client/load" \
  --data-urlencode 'datastar={"client_id":""}' -o "$load" &
loadpid=$!
cid=""
for _ in $(seq 1 60); do
  cid=$(grep -oE '[0-9a-f]{32}' "$load" 2>/dev/null | head -1)
  [ -n "$cid" ] && break
  sleep 1
done
[ -n "$cid" ] || { echo "never got a client id"; kill "$loadpid" 2>/dev/null; exit 2; }

t0=$(date +%s)
# --max-time makes curl hang up mid-stream, which is the whole point: the server
# must not care.
curl -s -N --max-time "$abort_after" -H 'Datastar-Request: 1' -G "$base/client/search" \
  --data-urlencode "datastar={\"search_term\":\"$url\",\"client_id\":\"$cid\"}" \
  -o /dev/null 2>/dev/null
echo "client $cid: request aborted after ${abort_after}s (browser navigation)"

deadline=$((t0 + wait_for))
seen=""
while [ "$(date +%s)" -lt "$deadline" ]; do
  now=$(find "$music" -name '*.mp3' 2>/dev/null | wc -l)
  if [ "$now" -gt "$before" ]; then
    seen=$(find "$music" -name '*.mp3' -newermt "@$t0" -printf '%f\n' 2>/dev/null | head -1)
    break
  fi
  sleep 2
done
first=$(( $(date +%s) - t0 ))

# RFC3339, not "@epoch": docker rejects the @ form outright ("invalid value for
# since"), and the error on stderr used to fall straight into the grep, so this
# counted zero attempts on every run, pass or fail. Docker timestamps in UTC.
since=$(date -u -d "@$((t0 - 5))" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$((t0 - 5))" +%Y-%m-%dT%H:%M:%SZ)
attempted=$(docker logs --since "$since" "$name" 2>&1 | grep -c "Downloading song")
echo "  download attempts in log : $attempted"
echo "  first file appeared after: ${first}s"
echo "  file                     : ${seen:-none}"

kill "$loadpid" 2>/dev/null
if [ -n "$seen" ]; then
  echo "PASS: the download outlived the navigation"
  exit 0
fi
echo "FAIL: nothing was written; the download died with the aborted request"
exit 1
