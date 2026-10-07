#!/bin/bash
# Measure how long the web UI stays unreachable while one search runs.
#   ./dev/ab-probe.sh <host-port> "<search term>"
#
# Fires a real /client/search (the endpoint the search box calls) in the
# background, then polls the home page every 2s for as long as that request is
# in flight. A patched server keeps answering; an unpatched one returns http 000
# for every poll until the search finishes.
port=${1:?usage: ab-probe.sh PORT "search term"}
term=${2:?missing search term}
base=http://127.0.0.1:$port
out=/tmp/ab-probe-$port.txt

printf '== %s  term: %s ==\n' "$base" "$term"
printf 'idle GET /           : %s\n' \
  "$(curl -o /dev/null -s -m 10 -w '%{http_code} in %{time_total}s' "$base/")"

start=$(date +%s)
curl -s -N --max-time 900 -H 'Datastar-Request: 1' -G "$base/client/search" \
  --data-urlencode "datastar={\"search_term\":\"$term\",\"client_id\":\"probe\"}" \
  -o "$out" &
probe=$!

sleep 3
samples=0 dead=0 worst=0
while kill -0 "$probe" 2>/dev/null; do
  r=$(curl -o /dev/null -s -m 15 -w '%{http_code} %{time_total}' "$base/")
  code=${r% *} t=${r#* }
  samples=$((samples + 1))
  [ "$code" = "000" ] && dead=$((dead + 1))
  worst=$(awk -v a="$t" -v b="$worst" 'BEGIN{print (a>b)?a:b}')
  sleep 2
done
wait "$probe"; rc=$?
end=$(date +%s)

printf 'search in flight     : %ss (curl rc=%s, %s bytes)\n' \
  "$((end - start))" "$rc" "$(stat -c%s "$out" 2>/dev/null || wc -c <"$out")"
printf 'GET / while searching: %s polls, %s unreachable, worst reply %ss\n' \
  "$samples" "$dead" "$worst"
printf 'songs rendered       : %s\n' "$(grep -c 'open.spotify.com/track' "$out")"
printf 'GET / after search   : %s\n' \
  "$(curl -o /dev/null -s -m 10 -w '%{http_code} in %{time_total}s' "$base/")"
