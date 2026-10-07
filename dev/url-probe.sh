#!/bin/bash
# Drive a real download through the web UI's own endpoints, the way a browser
# does, and report how long the request stays open after the file is written.
#   ./dev/url-probe.sh <host-port> <spotify-url>
#
# A browser first calls /client/load, which mints a client id and keeps an SSE
# stream open; the download only runs for a registered client. The interesting
# number is `written -> closed`: the mp3's mtime versus the moment the SSE
# request finally ends. Upstream has no `return` at the end of the url branch of
# handle_get_client_search(), so after the last track lands it falls through and
# spends one Spotify search on the url string itself, which matches nothing.
# Measured on a real album url: about 1s plus a stray empty result list, so a
# small number here is expected. It matters when it is not small, or when that
# wasted call fails and reports a successful download as an error.
port=${1:?usage: url-probe.sh PORT "spotify url"}
url=${2:?missing url}
base=http://127.0.0.1:$port
music=${MUSIC_DIR:?set MUSIC_DIR to the host dir mounted at /music}

load_out=/tmp/url-probe-$port-load.txt
curl -s -N --max-time 900 -H 'Datastar-Request: 1' -G "$base/client/load" \
  --data-urlencode 'datastar={"client_id":""}' -o "$load_out" &
loadpid=$!

cid=""
for _ in $(seq 1 60); do
  cid=$(grep -oE '[0-9a-f]{32}' "$load_out" 2>/dev/null | head -1)
  [ -n "$cid" ] && break
  sleep 1
done
[ -n "$cid" ] || { echo "never got a client id"; kill "$loadpid" 2>/dev/null; exit 1; }
echo "client id: $cid"

start=$(date +%s)
curl -s -N --max-time 900 -H 'Datastar-Request: 1' -G "$base/client/search" \
  --data-urlencode "datastar={\"search_term\":\"$url\",\"client_id\":\"$cid\"}" \
  -o "/tmp/url-probe-$port-dl.txt"
end=$(date +%s)
kill "$loadpid" 2>/dev/null

printf 'download request open : %ss\n' "$((end - start))"
mp3=$(find "$music" -name '*.mp3' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1)
if [ -n "$mp3" ]; then
  written=${mp3% *}; path=${mp3#* }
  printf 'file written          : %s\n' "$path"
  printf 'written -> closed     : %ss of spinner after the last byte was written\n' \
    "$(awk -v e="$end" -v w="$written" 'BEGIN{printf "%.0f", e - w}')"
else
  echo 'file written          : nothing appeared in the music dir'
fi
