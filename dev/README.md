# Re-deriving the patch

`../patches/patch_webui.py` is generated, not hand-written. It is a set of
anchored exact-match edits, and CI applies it at build time. When an upstream
spotdl release rewrites `web/routes.py` or `web/api.py`, the anchors stop
matching and the build fails with:

```
error: routes.py: rule 4 anchor is not present - upstream code has drifted.
       Re-derive the patch with dev/gen_patch.py.
```

That failure is the system working. The alternative — copying whole patched
files over upstream — would silently roll back whatever else upstream had
changed in those modules.

## Workflow

```bash
# 1. pull the current upstream source (needs docker)
./gen_patch.py extract                       # or --base <image@sha256:...>

# 2. re-apply the three fixes to the editable copy
$EDITOR work/web/routes.py work/web/api.py

# 3. regenerate
./gen_patch.py generate
```

`generate` refuses to write unless the result is self-consistent: applying the
new rules to pristine upstream source must reproduce `work/` byte for byte, must
be idempotent, and must leave both modules valid Python.

Offline / no docker: `./gen_patch.py extract --from-dir DIR` where `DIR` holds
`web/routes.py` and `web/api.py`.

## What the three fixes are

1. **Non-track URLs.** Route album/playlist/artist URLs to
   `spotdl.utils.search.parse_query()` instead of `Song.from_url()`, which
   rejects anything without `/track/`. See `resolve_songs()` in `work/web/routes.py`.
2. **Per-track isolation.** One unmatched or failing track must not abort the
   rest of an album, and progress is reported as `n/N`.
3. **Relaxed retry.** A track that fails is retried once with the audio
   providers' `filter_results` turned off, then the flag is restored. Obscure
   releases routinely score below spotdl's match threshold.

`work/web/api.py` additionally mirrors 1 and 2 in the `/api/download/url`
endpoint so the REST API behaves like the UI.

## Checking a rebuilt image

```bash
docker run --rm --entrypoint grep <image> -q "def resolve_songs" /app/spotdl/web/routes.py && echo patched
docker run --rm <image> --version                    # entrypoint passthrough
docker run --rm --entrypoint sh <image> -c 'command -v spotdl && spotdl --version'
```

`dev/.cache/` and `dev/work/` are local scratch and are git-ignored.
