# spotDL, with the web interface repaired.
#
# Two things are wrong with the upstream `spotdl/spotify-downloader` image:
#
#   1. The bundled yt-dlp goes stale within weeks and YouTube starts answering
#      the media requests with `HTTP Error 403: Forbidden`, which makes *every*
#      download fail at the audio stage - single tracks included.
#   2. The web UI cannot download albums or playlists even though its own home
#      page says it can: the download endpoint calls `Song.from_url()`, whose
#      first statement rejects any URL that is not a `/track/` URL.
#
# This Dockerfile does not rebuild spotdl. It stacks a thin repair layer on top
# of the published image, so upstream keeps owning everything else.

# Pinned by digest, never by tag: a moving `:latest` would let upstream change
# the code under the patch anchors. CI bumps this explicitly on every build.
ARG BASE_IMAGE=spotdl/spotify-downloader@sha256:b2ac304bdbf50d2ba23ffd490f922cdda99f5eeb8da170ff2d169e563dba43ed

FROM ${BASE_IMAGE}

ARG YT_DLP_SPEC=yt-dlp[default]>=2026.08.19
ARG IMAGE_VERSION=dev
ARG BASE_DIGEST=b2ac304bdbf50d2ba23ffd490f922cdda99f5eeb8da170ff2d169e563dba43ed

LABEL org.opencontainers.image.title="spotdl webui fixed" \
      org.opencontainers.image.description="spotDL with a working web UI for albums, playlists and artists, plus a current yt-dlp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.base.digest="${BASE_DIGEST}"

# --- 1. a yt-dlp that YouTube still answers ------------------------------------------------
#
# Installed as the spotdl user on purpose: /app/.venv belongs to uid 1000, and a
# `chown -R` to fix ownership afterwards would rewrite every file in a ~1.3 GB
# tree into a fresh layer. The version stays inside spotdl's own pin
# (`yt-dlp[default]>=2026.07.04,<2027`), so nothing else has to move.
USER spotdl
# No -U: pinning the spec without it lets uv move exactly one package, so
# requests/websockets/urllib3 stay exactly where upstream put them.
RUN uv pip install --python /app/.venv/bin/python --no-cache "${YT_DLP_SPEC}" \
 && /app/.venv/bin/python -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"

# --- 2. the web UI repair --------------------------------------------------------------
#
# Anchored, exact-match edits that run at build time. If upstream has changed
# these modules, the script exits non-zero and THE BUILD FAILS, rather than
# silently shipping a module that rolled back someone else's fix.
COPY --chown=spotdl:spotdl patches/patch_webui.py /tmp/patch_webui.py
RUN python3 /tmp/patch_webui.py --spotdl-dir /app/spotdl \
 && rm -rf /app/spotdl/web/__pycache__ /tmp/patch_webui.py

# --- 3. `spotdl` available in `docker exec` ---------------------------------------------
#
# Upstream's ENTRYPOINT is `uv run --project /app ... spotdl`, so the console
# script only exists on PATH inside that one process; a plain
# `docker exec -it <c> bash` gets "spotdl: command not found".
COPY --chmod=0755 launcher/spotdl /usr/local/bin/spotdl

# Guard rail: never publish an image where the patch did not actually land.
RUN grep -q "def resolve_songs" /app/spotdl/web/routes.py \
 && grep -q "download_song_with_retry" /app/spotdl/web/api.py \
 && spotdl --version \
 && /app/.venv/bin/yt-dlp --version

# ENTRYPOINT, WORKDIR /music, USER and the 8800 port are all inherited from the
# upstream image, so `docker run <this-image> web --host 0.0.0.0` behaves
# exactly like upstream.
USER spotdl
