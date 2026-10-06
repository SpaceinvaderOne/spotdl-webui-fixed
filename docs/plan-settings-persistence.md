# Plan — make the web UI settings actually persist (and maybe reorder)

Status: **proposed**, not started. Scope owner: this repo's build-time patch
(`patches/patch_webui.py`, generated from `dev/work/`).

Everything quoted below was read out of the running image
(`ghcr.io/spaceinvaderone/spotdl-webui-fixed:latest`, spotdl 4.5.2), not from
memory or upstream docs.

---

## 1. Problem

The web UI's *Download Settings* dialog shows a green **"Changes saved"** banner
without saving anything. From `spotdl/web/routes.py`:

```python
@router.post("/client/settings")
@datastar_response
async def handle_post_client_settings(datastar_signals: ReadSignals):
    ...
    if signals.downloader_settings is not None:
        client.downloader_settings = signals.downloader_settings   # in-memory only
    yield SSE.patch_elements(""" ... <span>Changes saved</span> ... """)
```

Corroborating evidence that nothing else saves it either:

- `grep -rn "def save_settings" /app/spotdl --include=*.py` → **no matches**.
- `spotdl/utils/config.py` exposes `get_config_file()` (line 87) and `get_config()`
  (line 175, `json.load`). Read-only surface.
- The only writer of `config.json` is `generate_initial_config()` in
  `spotdl/utils/console.py`, which creates it with defaults **when it does not
  exist**.

Second, independent problem: even if it were written, the change would not apply
to the running process. `Downloader.__init__` (`spotdl/download/downloader.py`)
builds provider instances once:

```python
self.lyrics_providers: List[LyricsProvider] = []
for lyrics_provider in self.settings["lyrics_providers"]:
    lyrics_class = LYRICS_PROVIDERS.get(lyrics_provider)
    if lyrics_class is None:
        raise DownloaderError(f"Invalid lyrics provider: {lyrics_provider}")
    if lyrics_provider == "genius":
        access_token = self.settings.get("genius_token")
        if not access_token:
            raise DownloaderError("Genius token not found in settings")
        self.lyrics_providers.append(Genius(access_token))
    else:
        self.lyrics_providers.append(lyrics_class())

self.audio_providers: List[AudioProvider] = []
for audio_provider in self.settings["audio_providers"]:
    audio_class = AUDIO_PROVIDERS.get(audio_provider)
    ...
    self.audio_providers.append(
        audio_class(
            output_format=self.settings["format"],
            cookie_file=self.settings["cookie_file"],
            search_query=self.settings["search_query"],
            filter_results=self.settings["filter_results"],
            yt_dlp_args=self.settings["yt_dlp_args"],
        )
    )
```

Third problem, cosmetic but confusing: the dialog renders from the module-level
`AUDIO_PROVIDERS` / `LYRICS_PROVIDERS` dicts, so its row order is a fixed display
order. A user cannot see — let alone set — the order the providers are actually
tried in. Observed on a real install: dialog showed lyrics as
`genius, musixmatch, azlyrics`, while `config.json` said `genius, azlyrics,
musixmatch`.

### Observed symptoms this fixes

- User ticks a provider, presses Save, sees "Changes saved", restarts, and the
  tick is gone.
- Changing output format or bitrate in the dialog does nothing after a restart.
- Provider order can only be changed by editing a file and restarting, which
  nothing in the UI hints at.

---

## 2. Goals and non-goals

**Goals**

1. `POST /client/settings` writes `config.json`, and the banner reflects the
   truth.
2. Provider list, output format and bitrate take effect **without a restart**.
3. A write failure is visible and never corrupts the existing file.
4. Everything stays inside the existing anchored-patch mechanism, so upstream
   drift still fails the build loudly.

**Non-goals**

- Not redesigning the dialog.
- Not adding drag-and-drop (see §5, deliberately deferred).
- Not fixing the community Unraid template (separate repo, already handled by
  `unraid/switch-to-image.py`).
- Not persisting per-client state — one shared `config.json` is the correct
  model here; spotdl has one config file, and multi-user isolation is out of
  scope for a self-hosted downloader.

---

## 3. Design

### 3.1 Persistence helper

Add to `spotdl/web/routes.py` (pure insertion, module level, next to the other
helpers so `gen_patch.py` gives it a borrowed-context anchor):

```python
def save_downloader_settings(settings: Dict[str, Any]) -> None:
    """
    Merge `settings` into config.json.

    Read-modify-write rather than overwrite: the file may hold keys the web UI
    never shows (genius_token, spotify credentials, manual yt_dlp_args), and
    clobbering them would break a working install the first time anyone pressed
    Save.
    """
    config_file = get_config_file()
    current = {}
    if config_file.is_file():
        with open(config_file, "r", encoding="utf-8") as f:
            current = json.load(f)

    current.update(settings)

    # Write-then-rename. A plain open(..., "w") truncates first, so a full disk
    # or a crash mid-write leaves the user with no config at all and spotdl
    # regenerates defaults - settings silently reset instead of loudly failed.
    tmp = config_file.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, config_file)
```

Supporting edits in the same file:

- imports: `import json`, `import os`, and `get_config_file` added to the
  existing `from spotdl.utils.config import get_spotdl_path` line.
- `handle_post_client_settings`: wrap the assignment + save, and branch the
  banner on the outcome (§3.4).

**Keys written.** The whole `downloader_settings` dict, not a filtered subset.
It is built by spotdl from the same `DownloaderOptions` shape the CLI uses, so
there is no "UI-only key" to filter out, and a deny-list would silently rot as
upstream adds options. The read-modify-write keeps keys that never entered the
dict at all.

**Failure handling.** `OSError` (read-only mount, `EPERM` on a uid mismatch,
ENOSPC) and `json.JSONDecodeError` (hand-edited file with a trailing comma) are
caught, logged with `app_state.logger.warning`, and surfaced as the error banner.

On `JSONDecodeError` the file is **not** overwritten, and the reason is worth
stating precisely because `get_config()` has no such protection:

```python
if not config_path.exists():
    raise ConfigError("Config file not found. ...")
with open(config_path, "r", encoding="utf-8") as config_file:
    return json.load(config_file)      # no try/except: a malformed file propagates
```

So a corrupt config is already a broken install rather than a silently
degraded one. Overwriting it during a failed Save would turn "fix your JSON"
into "your settings are gone and you do not know what they were", which is the
strictly worse outcome. Preserve the bytes, report the problem.

**Concurrency.** Two tabs open, both press Save → last writer wins per key, no
merge conflicts to resolve because the merge is per-key on the whole dict. Not
worth locking; documented in the module docstring.

### 3.2 Applying without a restart

The downloader object is reachable as `client.downloader` (`Client` is defined in
`spotdl/utils/web.py:72`), and upstream already mutates it from the web layer —
`routes.py:414` does `client.downloader.settings["output"] = ...`. So mutating it
is in keeping with the existing design rather than a new violation.

```python
def apply_providers(downloader: Downloader, settings: Dict[str, Any]) -> None:
    """
    Rebuild the provider lists so a settings change takes effect immediately.

    The new list is built completely before anything is assigned: if a provider
    name is unknown, or genius is selected with no token, DownloaderError
    propagates and the running downloader keeps the providers it had. Without
    that the failure mode is an empty provider list and a dead server.
    """
    audio = []
    for name in settings["audio_providers"]:
        cls = AUDIO_PROVIDERS.get(name)
        if cls is None:
            raise DownloaderError(f"Invalid audio provider: {name}")
        audio.append(
            cls(
                output_format=settings["format"],
                cookie_file=settings["cookie_file"],
                search_query=settings["search_query"],
                filter_results=settings["filter_results"],
                yt_dlp_args=settings["yt_dlp_args"],
            )
        )

    lyrics = []
    for name in settings["lyrics_providers"]:
        cls = LYRICS_PROVIDERS.get(name)
        if cls is None:
            raise DownloaderError(f"Invalid lyrics provider: {name}")
        lyrics.append(
            Genius(settings["genius_token"]) if name == "genius" else cls()
        )

    if not audio:
        raise DownloaderError("No audio providers specified")

    downloader.settings.update(settings)
    downloader.audio_providers = audio
    downloader.lyrics_providers = lyrics
```

Notes:

- Mirrors `Downloader.__init__` exactly, including the Genius special case and
  the empty-audio guard.
- Import cost is small, which matters because imports are patch anchors:
  `AUDIO_PROVIDERS` and `LYRICS_PROVIDERS` are already imported in `routes.py`
  from `spotdl.download.downloader`, and `DownloaderError` is defined in that
  same module (line 86) — so it is one extra name on an existing line. `Genius`
  comes from `spotdl.providers.lyrics`, and `Downloader` from
  `spotdl.download.downloader`: one new import line total.
- **Genius needs a token.** Real installs have one: spotdl ships a public default
  in `config.json` (confirmed present on the reference Unraid box). A user who
  deleted it must not be able to wedge the server, hence build-then-assign and
  the error banner.
- `downloader.settings.update(settings)` is what makes `bitrate`, `output`,
  `format`, `cookie_file` and friends apply on the next download; provider
  construction is what makes `format` stick for the providers that captured it
  at construction time.
- Interaction with the existing patch: `download_song_with_retry` iterates
  `downloader.audio_providers` and toggles `filter_results` on each instance.
  Replacing the list is safe because the retry helper reads the attribute at call
  time, but it must **not** cache a copy — worth a regression test (T7).
- Anything with a download in flight keeps its old providers; that is correct.

### 3.3 Restart is still required for

`host`, `port`, `enable_tls`, `log_level`, and `client_id` handling — none are in
this dialog. Spotify credentials change is out of scope.

### 3.4 The banner tells the truth

`handle_post_client_settings` becomes:

```python
saved = True
if signals.downloader_settings is not None:
    try:
        apply_providers(client.downloader, signals.downloader_settings)
        client.downloader_settings = signals.downloader_settings
        save_downloader_settings(signals.downloader_settings)
    except (DownloaderError, OSError, json.JSONDecodeError) as exc:
        saved = False
        app_state.logger.warning("Could not save settings: %s", exc)
```

then choose between the two blocks that **already exist** in this file: the
`settings-is-saved` alert and the `settings-is-not-saved` alert (currently only
rendered in the `client is None` branch). If `not saved`, the settings dict is
also not reassigned, so the client's `patch_signals` round-trip restores the
checkboxes to what the server actually is running — no drift between the form
and reality.

The existing `await asyncio.sleep(3)` + clear stays untouched.

---

## 4. Patch mechanics

- Edit `dev/work/web/routes.py` only (plus `api.py` if the REST `/api/settings`
  surface should mirror it — decision: **no**, it has no settings endpoint).
- Regenerate with `./dev/gen_patch.py generate`. It refuses to write unless the
  emitted rules reproduce `dev/work/` byte-for-byte, are idempotent, and leave
  the module compiling.
- Estimated rules on `web/routes.py`: 2 imports, 2 new functions (insertions),
  1 handler body replacement → **~5 new rules**, on top of the 4 already there.
  `web/api.py` unchanged at 5. Total 14 rules.
- Drift surface roughly doubles on this file. That is the real cost of this plan,
  and it is the reason §5 is deferred.
- Bump `VERSION` in `dev/gen_patch.py` to `4.5.2-r2`; it is echoed by the build
  log and by `patch_webui.py`, which is how you tell an image apart at a glance.
- CI tags are unaffected (they key off yt-dlp + spotdl versions). The skip rule
  already builds on every push, so the change ships on the next run without
  `force`.

---

## 5. Ordering UI — deferred, and why

Cheap version: render the selected providers **in stored order** with ▲▼ buttons
above a plain checkbox list of the unselected ones.

```jinja
{% for provider in downloader_settings.audio_providers %}
  <li>{{ loop.index }}. {{ provider }}
      <button data-on-click="move($downloader_settings.audio_providers, {{ loop.index0 }}, -1)">▲</button>
      <button data-on-click="move($downloader_settings.audio_providers, {{ loop.index0 }},  1)">▼</button>
  </li>
{% endfor %}
```

plus a small `move()` helper in `static/` (datastar `splice` on the signal) and
the same treatment for lyrics.

Roughly 40 lines across two template files and one static asset, all new anchors.
The underlying capability — arbitrary order — already works today via the config
array, which is what a real deployment uses once. Recommended sequence: ship
persistence, watch whether anyone asks to reorder, and only then spend the drift
budget. A PR that also rewrites two templates is a PR upstream is less likely to
take.

---

## 6. Tests

The UI is datastar: **every** request must send `Datastar-Request: 1`, or
`handle_signals` logs "No signals provided" and you get an empty settings dict.
This costs an hour to rediscover if it is not written down.

```bash
BASE=http://127.0.0.1:8800
CID=$(curl -s -H "Datastar-Request: 1" -G "$BASE/client/load" \
        --data-urlencode 'datastar={"client_id":""}' \
      | grep -oE '"client_id":"[a-f0-9]+"' | head -1 | cut -d'"' -f4)
```

| # | Test | Method | Expected |
|---|---|---|---|
| T1 | patch applies | `python3 patches/patch_webui.py --spotdl-dir <pristine>` | `patch OK`, all rules applied |
| T2 | idempotent | run T1's command twice | second run: `already patched (0/N rules)` |
| T3 | drift fails | edit one anchor line in a copy of pristine source | exit 1, names the rule |
| T4 | save persists | POST settings with `["bandcamp"]`, then `docker restart` | `GET /client/settings` returns `["bandcamp"]` after restart; `config.json` on the host shows it |
| T5 | unknown keys survive | add `"canary": 42` to `config.json`, POST settings | key still present, value unchanged |
| T6 | corrupt file not clobbered | write `{ invalid json` to config.json, POST settings | error banner, file byte-identical afterwards |
| T7 | live rebuild (behavioural) | POST providers `["bandcamp"]` only, download a track bandcamp does not have | fails with a bandcamp-only error; then `["youtube-music","youtube","bandcamp"]`, same track succeeds — **without a restart** |
| T8 | provider list identity | `docker exec <c> python -c` importing the running app's state is not possible; instead assert via T7 plus a `logger.debug` line added by the patch listing rebuilt providers | log line matches posted order |
| T9 | invalid name rejected | POST `audio_providers: ["nope"]` | error banner, `GET` returns previous list, container stays up |
| T10 | genius without token | remove `genius_token`, POST with genius selected | error banner, providers unchanged, container stays up |
| T11 | read-only config | `chmod a-w` on the mounted config dir, POST | error banner (EPERM), no traceback in logs, container stays up |
| T12 | retry helper still works | download an album whose tracks need the relaxed retry (the Drax album, track 4 `Phosphene`) | completes; `filter_results` restored on the *new* provider objects |
| T13 | two tabs | two clients POST different values | last write wins, no exception, no key loss vs T5 |
| T14 | UI sanity | open the dialog in a browser, tick, Save, reload | banner accurate; after reload the ticks match the server |
| T15 | smoke in CI | existing CI smoke step | still green; optionally extend it to assert `def save_downloader_settings` in `routes.py` |

T4, T7, T9, T11 and T12 are the ones that actually prove the feature; the rest
are regression walls.

---

## 7. Rollout

1. Branch, implement in `dev/work/`, `./dev/gen_patch.py generate`.
2. `docker build` on basestar, run T1-T13 there on port 8801 with a throwaway
   music/config dir (`chown` it to the run uid — a root-owned bind mount is the
   single most common false failure in this test series).
3. Commit, push, watch Actions run to green, confirm the new `latest` digest.
4. Reference box: `docker pull` + recreate, then re-run T4 and T12 against it.
5. Update `README.md`: the "the dialog does not save" bullet becomes "it does now,
   and providers apply without a restart"; keep a note that `config.json` is the
   source of truth and that `--user` must match the file's ownership.

**Rollback:** revert the commit and let CI republish; `latest` moves back. No
data migration exists, so nothing to unwind on client boxes — `config.json` is
compatible in both directions because the write is a superset of what spotdl
itself writes.

---

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Doubling anchors on `routes.py` | more frequent red CI after upstream releases | re-derivation is already scripted (`dev/gen_patch.py`); CI opens an issue automatically on drift |
| Overwriting hand-edited config the UI never shows | silent loss of `genius_token`, credentials, `yt_dlp_args` | read-modify-write (T5) |
| Truncated config on crash/full disk | spotdl regenerates defaults, user blames the image | temp file + `os.replace` (T6) |
| Rebuild raises mid-change | providers cleared, server unusable until restart | build-then-assign, error banner (T9, T10) |
| uid mismatch on the mounted config dir | `EPERM` on save | error banner, not a traceback; documented in README (T11) |
| Writing on every Save, per client | trivial (a few KB) | none needed |
| Upstream implements it differently, patch conflicts forever | maintenance | keep the patch to 5 rules, file the upstream PR first so the eventual removal is clean |

---

## 9. Upstream

The false "Changes saved" banner is a bug on its own terms, independent of this
image. Sequence:

1. Open an issue quoting `handle_post_client_settings`, noting no writer for
   `config.json` exists in 4.5.2 and that the success banner renders
   unconditionally. Attach the four-line repro (load client, POST settings,
   restart, observe).
2. Open a PR with §3.1 + §3.2 + §3.4, tests T4/T5/T9 as pytest against a temp
   `HOME`, and **without** the ordering UI — a small PR that fixes a lie has a
   much better chance than one that also redesigns a dialog.
3. Keep the ordering idea as a separate discussion issue referencing this repo,
   so if upstream takes the persistence fix, the anchor count in this image drops
   back toward zero.

MIT, so vendoring the diff meanwhile is unproblematic; the header comment in
`patch_webui.py` should carry the issue/PR link so future maintainers know which
rules are meant to be temporary.

---

## 10. Sequencing and effort

| Step | Effort |
|---|---|
| §3.1 save helper + import edits | 30 min |
| §3.2 apply without restart | 45 min |
| §3.4 honest banner | 15 min |
| Regenerate patch, T1-T3 | 15 min |
| T4-T13 on the test container | 60-90 min (T7/T12 wait on YouTube) |
| README + version bump, push, CI, reference box | 30 min |
| Upstream issue + PR write-up | 45 min |
| **Total** | **about half a day** |

Order of work matters: §3.2 before §3.1, because building and testing the
provider rebuild is where the interesting failures are (Genius token, provider
ctor kwargs, the retry helper's list identity), and none of that depends on disk
writes.

## 11. Acceptance criteria

- [ ] POST settings → `config.json` on the host changes, unknown keys intact.
- [ ] Values survive `docker restart`.
- [ ] Provider order and format changes work **without** a restart (T7).
- [ ] The success banner appears only when the write succeeded.
- [ ] Invalid input cannot take the container down.
- [ ] `./dev/gen_patch.py generate` self-test passes; patch still fails loudly on
      simulated drift.
- [ ] CI green, `latest` republished, reference box verified against the ghcr
      image rather than a locally built tag.
