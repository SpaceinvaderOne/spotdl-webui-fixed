# spotdl-webui-fixed

A drop-in replacement for [`spotdl/spotify-downloader`](https://hub.docker.com/r/spotdl/spotify-downloader)
whose **web interface can actually download albums and playlists**, and whose
**yt-dlp is kept current automatically**.

Nothing here rebuilds spotdl. It stacks a small repair layer on the published
image, so upstream keeps owning everything else.

| | upstream image | this image |
|---|---|---|
| paste an album/playlist URL into the web UI | `Invalid URL` | downloads the whole thing |
| one unmatched track in an album | aborts the album | skipped, rest continue |
| yt-dlp | frozen at image build date | rebuilt from PyPI on a schedule |
| `docker exec <c> spotdl` | `command not found` | works |

## Quick start

```bash
mkdir -p music config && sudo chown -R 99:100 music config
docker compose -f compose.example.yml up -d
```

Or plainly (`--user 99:100` is `nobody:users`, Unraid's convention; drop the
flag on a normal Linux host and it runs as the image's own uid 1000):

```bash
docker run -d --name spotdl --restart unless-stopped \
  --user 99:100 \
  -p 8800:8800 \
  -v "$PWD/music:/music" \
  -v "$PWD/config:/home/spotdl/.config/spotdl" \
  ghcr.io/spaceinvaderone/spotdl-webui-fixed:latest \
  web --host 0.0.0.0 --web-use-output-dir
```

Then open `http://<host>:8800`, paste a Spotify **track, album, playlist or
artist** URL into the search box and hit enter. It redirects to *Downloads*,
where each track gets its own progress bar and the URL's button shows `n/N`
while the batch runs.

CLI too, if you prefer it:

```bash
docker exec -it spotdl spotdl download "https://open.spotify.com/album/..." \
  --output "{artist}/{album}/{track-number} - {title}.{output-ext}"
```

### Which user should run it?

Whatever uid you pick, the container writes your music as that uid. It does not
have to match the music directory - an Unraid array share is mounted `0777`, so
any uid can write into it. What changes is who ends up **owning** the files, and
on Unraid that matters:

| `--user` | files land as | use when |
|---|---|---|
| `99:100` | `nobody:users` | **Unraid.** Matches the array and every other container; `newperms`, mover and the permissions checker all agree with it. |
| `1000:1000` | your user | A normal Linux host where the library belongs to a real account. |
| *(omitted)* | `1000:1000` | Same as above - it is the uid baked into the image. |

Verified on a real Unraid array, same track both times:

```
--user 1000:1000  ->  ed:1000        Drax - Interior.mp3
--user 99:100     ->  nobody:users   Drax - Interior.mp3
```

The upstream image cannot run under `--user 99:100` at all: a numeric uid with
no `/etc/passwd` entry gets `HOME=/` and spotdl tries to create `/.config`, then
uv's cache is owned by uid 1000 and the entrypoint cannot touch it. This image
sets `HOME` and moves uv's cache to `/tmp`, so `99:100`, `1000:1000` and root
all work with **no extra flags or environment variables**. Note that spotdl
ignores `PUID`/`PGID` entirely - those variables are a LinuxServer.io
convention, and setting them does nothing here. Use `--user`.

The only requirement: chown the two directories you mount to the same uid.

```bash
sudo chown -R 99:100 /path/to/music /path/to/config
```

### Tags

| tag | meaning |
|---|---|
| `latest` | newest successful build |
| `2026.08.19-spotdl4.5.2` | exact yt-dlp + spotdl pair, never re-pointed |
| `spotdl4.5.2` | newest yt-dlp for that spotdl release |

Pin a versioned tag if you want predictability; pin `latest` if you want the
yt-dlp refresh and are happy to `docker pull` (see *Staying current*).

## What is actually broken upstream

**1. The download endpoint only understands single tracks.**
`spotdl/web/api.py` and `spotdl/web/routes.py` both hand the URL to
`Song.from_url()`, which opens with:

```python
if "open.spotify.com" not in url or "track" not in url:
    raise SongError(f"Invalid URL: {url}")
```

Meanwhile `validate_search_term()` cheerfully accepts `/album/`, `/playlist/`
and `/artist/` URLs and routes them there, `home.html.j2` advertises "a song or
an entire album, artist, or playlist", and `api.py` imports `Album`, `Playlist`
and `Artist` without ever using them. The feature was scaffolded and never
wired up. This image routes non-track URLs through
`spotdl.utils.search.parse_query()` — the same dispatcher `spotdl download`
uses — so the web UI and the CLI now accept the same inputs.

**2. yt-dlp goes stale and YouTube returns `HTTP Error 403: Forbidden`.**
This is the one that makes *nothing* download, including single tracks, and it
looks like a Spotify or spotdl failure from the outside:

```
AudioProviderError: YT-DLP download error - https://music.youtube.com/watch?v=...
```

An image built in July carries a yt-dlp from July; YouTube moves monthly. The
fix is simply a current yt-dlp, which stays inside spotdl's own dependency pin
(`yt-dlp[default]>=2026.07.04,<2027`).

**3. Matching is too strict for obscure releases** (bonus, not upstream-breaking).
spotdl scores audio-provider hits against Spotify metadata and throws away
anything below its threshold. Compilations, remixes and tracks with long
parenthesised titles frequently score just under it and fail with
`No results found`. Rather than disabling the filter globally — which silently
grabs whatever YouTube Music ranks #1 — each failed track is retried once with
the filter relaxed. On a 14-track techno compilation that was the difference
between 5 tracks and 14.

## Staying current

The image is rebuilt **daily** by `.github/workflows/build.yml`, which resolves
the newest yt-dlp from PyPI and the current upstream image digest, runs smoke
tests, and publishes. Rebuilding is only half of it though — *containers do not
update themselves*:

```bash
docker pull ghcr.io/spaceinvaderone/spotdl-webui-fixed:latest && docker compose up -d
```

Or let [watchtower](https://containrrr.dev/watchtower/) do it on a schedule.
Unraid users can use *Compose Manager* / *Update Assistant*, or the built-in
"app updates" check.

## Two traps

- **Do not override `entrypoint:`.** Upstream runs
  `uv run --project /app --no-dev --frozen --no-sync spotdl`. That `--no-sync`
  is what stops uv from re-reading `uv.lock` at startup and downgrading yt-dlp
  back to the image's original version. Override the entrypoint and the 403 fix
  silently reverses. Pass extra flags via `command:` (or the container's args)
  instead.
- **Settings live in `~/.config/spotdl/config.json`**, not in env vars. Map
  `/home/spotdl/.config/spotdl` to a host directory or every recreate resets
  format, bitrate and output layout. If you came here from the
  [community Unraid template](https://github.com/MROGHUB/unraid-templates), note
  that its *Output Format*, *Bitrate* and *Disable Bitrate Conversion* fields are
  declared as `Type="Variable"` with targets like `/etc/spotdl/config.json`, so
  they only create junk environment variables — **they do nothing**. Use the web
  UI's Settings page.

Useful settings, in the web UI under Settings:

| setting | default | note |
|---|---|---|
| `output` | `{artists} - {title}.{output-ext}` | try `{artist}/{album}/{track-number} - {title}.{output-ext}` for an organised library |
| `bitrate` / `format` | `128k` / `mp3` | re-encoded from YouTube's Opus; `320k` is mostly re-encode headroom |
| `cookie_file` | none | if YouTube starts bot-checking you, export cookies from a browser and map the file. Map a **file**, not a directory |

## When CI goes red

`patches/patch_webui.py` applies anchored, exact-match edits at build time and
**exits non-zero when upstream code no longer matches**, rather than overwriting
whole files and silently reverting someone else's fix. So a red build after an
upstream release usually means "the anchors need re-deriving", not "the build is
broken". See [`dev/README.md`](dev/README.md).

## Troubleshooting

**Container exits immediately with `PermissionError: [Errno 13] ... config.json`.**
The host directory behind `/home/spotdl/.config/spotdl` is not writable by the
uid the container runs as. A bind mount keeps the *host* ownership, so a
directory created by root breaks the container on first start:

```bash
sudo chown -R 99:100 ./config ./music     # or 1000:1000, to match `--user`
```

This is the one thing you must get right when switching uids. The image itself
handles the rest - `HOME`, uv's cache directory and the config path are all set
up so that `--user 99:100`, `--user 1000:1000` and root work unchanged.

**Everything fails with `AudioProviderError: YT-DLP download error` / `HTTP Error 403`.**
YouTube is refusing the request. On this image that usually means the container
has not been recreated since the last `docker pull` — check it:

```bash
docker exec spotdl /app/.venv/bin/yt-dlp --version
```

If the version is old, the image was pulled but the container was never
restarted (`docker pull` does not update a running container). If the version
is current, YouTube is rate-limiting your IP; a VPN, a proxy, or a browser
cookie file (`cookie_file` in Settings) will clear it.

**`spotdl: command not found` inside the container.** You are looking at the
upstream image, not this one — the launcher only exists here. Check
`docker inspect -f '{{.Config.Image}}' <container>`.

**Album downloads start but some tracks are skipped.** Those tracks have no
audio-provider match that spotdl trusts. Re-submit the same URL after fixing
network access: finished tracks are skipped, missing ones are fetched. The
server log names each track that failed.

## Licence

spotDL is MIT licensed by the spotDL Developers; this repository inherits that
licence and contains only a few dozen lines of repair code plus CI. Original
work © the spotDL Developers — please support them, and open issues upstream
for anything that belongs there.
