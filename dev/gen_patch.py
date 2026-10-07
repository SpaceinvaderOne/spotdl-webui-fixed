#!/usr/bin/env python3
"""
Regenerate ../patches/patch_webui.py after upstream spotdl changes.

The shipped patch is a set of anchored exact-match edits. When upstream rewrites
the web UI those anchors stop matching and CI fails on purpose. Re-derive it
like this:

    ./dev/gen_patch.py extract            # pull the current upstream files
    $EDITOR dev/work/web/routes.py        # make the edits
    ./dev/gen_patch.py generate           # re-emit patch_webui.py, self-tested

`generate` refuses to write anything unless the emitted patch round-trips:
applying it to pristine upstream source must reproduce dev/work/ byte for byte,
and must be idempotent.

Extraction uses `docker` by default. `--from-dir DIR` reads
DIR/web/routes.py and DIR/web/api.py instead, for offline runs.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import subprocess
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parent
ROOT = DEV.parent

CACHE = DEV / ".cache" # pristine upstream source, never edited
WORK = DEV / "work" # your edited copy
TARGETS = ("web/routes.py", "web/api.py")

DEFAULT_BASE = (
    "spotdl/spotify-downloader@sha256:"
    "b2ac304bdbf50d2ba23ffd490f922cdda99f5eeb8da170ff2d169e563dba43ed"
)

VERSION = "4.5.2-r3"

# Hunks separated by at most this many unchanged lines are merged into one rule
# so anchors stay long enough to be unambiguous.
MERGE_GAP = 4

# Lines of neighbouring context borrowed to anchor a pure insertion. Kept below
# MERGE_GAP so borrowed context can never overlap an adjacent rule.
CTX = 3


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def cmd_extract(args: argparse.Namespace) -> int:
    for target in TARGETS:
        if args.from_dir:
            source = Path(args.from_dir) / target
            if not source.is_file():
                print(f"error: {source} not found", file=sys.stderr)
                return 2
            text = source.read_text()
        else:
            text = run(
                [
                    "docker", "run", "--rm", "--entrypoint", "cat",
                    args.base, f"/app/spotdl/{target}",
                ]
            )

        for dest in (CACHE / target, WORK / target):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
        print(f"extracted {target}")

    print(f"\npristine -> {CACHE}\neditable -> {WORK}")
    print("edit the files in dev/work/, then run:  ./dev/gen_patch.py generate")
    return 0


# --------------------------------------------------------------------------- #
# rule construction
# --------------------------------------------------------------------------- #
def make_rule(a, b, i1, i2, j1, j2):
    new_text = "".join(b[j1:j2])

    if i2 > i1:  # replaces real source lines
        return "".join(a[i1:i2]), new_text

    # Pure insertion: borrow the following lines so the edit stays an
    # exact-match replacement that fails loudly when upstream drifts.
    k = min(CTX, len(a) - i1)
    if k:
        anchor = "".join(a[i1 : i1 + k])
        return anchor, new_text + anchor

    # Insertion at end of file: borrow the preceding lines instead.
    k = min(CTX, i1)
    anchor = "".join(a[i1 - k : i1])
    return anchor, anchor + new_text


def rules_for(pristine: str, patched: str):
    a = pristine.splitlines(keepends=True)
    b = patched.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    if not ops:
        raise SystemExit("no differences found - nothing to patch")

    merged = [list(ops[0])]
    for op in ops[1:]:
        last = merged[-1]
        if op[1] - last[2] <= MERGE_GAP:
            last[2] = op[2]
            last[4] = op[4]
        else:
            merged.append(list(op))

    rules = []
    for _, i1, i2, j1, j2 in merged:
        anchor, repl = make_rule(a, b, i1, i2, j1, j2)
        if not anchor.strip() or not repl.strip():
            raise SystemExit(f"empty anchor/replacement for a hunk near line {i1}")
        if "'''" in anchor or "'''" in repl:
            raise SystemExit("snippet contains '''; encode it differently")
        if anchor in pristine and pristine.count(anchor) != 1 and i2 > i1:
            raise SystemExit(f"anchor near line {i1} is not unique")
        rules.append((anchor, repl))
    return rules


def quote(text: str) -> str:
    """Embed `text` as a raw triple-single-quoted literal, byte for byte."""
    terminator = "'''" if text.endswith("\n") else "\n'''"
    return "r'''" + text + terminator


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #
def cmd_generate(_args: argparse.Namespace) -> int:
    all_rules: dict[str, list[tuple[str, str]]] = {}

    for target in TARGETS:
        pristine_path, work_path = CACHE / target, WORK / target
        for path in (pristine_path, work_path):
            if not path.is_file():
                print(f"error: {path} missing - run ./dev/gen_patch.py extract", file=sys.stderr)
                return 2
        all_rules[target] = rules_for(pristine_path.read_text(), work_path.read_text())
        print(f"{target}: {len(all_rules[target])} rule(s)")

    out = ROOT / "patches" / "patch_webui.py"
    out.write_text(render(all_rules))

    return self_test(all_rules)


def render(rules_by_target: dict[str, list[tuple[str, str]]]) -> str:
    parts = [HEADER.replace("VERSION_PLACEHOLDER", VERSION)]
    for target, rules in rules_by_target.items():
        parts.append(f"\nRULES[{target!r}] = [\n")
        for anchor, repl in rules:
            parts.append(f"    (\n        {quote(anchor)},\n        {quote(repl)},\n    ),\n")
        parts.append("]\n")
    parts.append(FOOTER)
    return "".join(parts)


def self_test(rules_by_target: dict[str, list[tuple[str, str]]]) -> int:
    """Apply the emitted script to pristine source and demand an exact match."""

    import importlib.util
    import tempfile

    script = ROOT / "patches" / "patch_webui.py"
    spec = importlib.util.spec_from_file_location("patch_webui", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "spotdl"
        for target, rules in rules_by_target.items():
            path = root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CACHE / target, path)

            applied, problems = module.apply(path, rules)
            if problems:
                print(f"self-test failed for {target}: {problems}", file=sys.stderr)
                return 1
            if path.read_text() != (WORK / target).read_text():
                print(
                    f"self-test failed: applying the patch to pristine upstream "
                    f"source does not reproduce dev/work/{target}",
                    file=sys.stderr,
                )
                return 1

            again, problems = module.apply(path, rules)
            if problems or again:
                print(f"self-test failed: {target} is not idempotent", file=sys.stderr)
                return 1

        for target in TARGETS:
            # the patched module must still be valid Python
            compile((root / target).read_text(), target, "exec")

    print(f"self-test OK: {out_lines()} lines -> {script}")
    return 0


def out_lines() -> int:
    return len((ROOT / "patches" / "patch_webui.py").read_text().splitlines())


HEADER = '''#!/usr/bin/env python3
"""
Repair the spotDL web interface so it can download what it advertises.

Generated by dev/gen_patch.py - do not hand-edit; regenerate instead.
Base spotdl version: VERSION_PLACEHOLDER

Applied at image build time on top of the upstream `spotdl/spotify-downloader`
image. Each edit is an anchored, exact-match replacement:

  * anchor present            -> the edit is applied
  * edit already present      -> nothing to do (idempotent)
  * anything else             -> exit non-zero with a message, so the build
                                 FAILS loudly instead of shipping a module that
                                 silently reverted unrelated upstream changes

Fixes
-----
1. Album / playlist / artist URLs. `Song.from_url()` starts with

       if "open.spotify.com" not in url or "track" not in url:
           raise SongError(f"Invalid URL: {url}")

   so every non-track URL is rejected even though `validate_search_term()`
   accepts them, `home.html.j2` advertises them, and `web/api.py` already
   imports `Album`, `Playlist` and `Artist` without using them. URLs now go
   through `spotdl.utils.search.parse_query`, the dispatcher used by
   `spotdl download`.

2. A single unmatched track used to abort the whole album. Tracks download
   independently now, with progress reported per track.

3. Releases whose audio-provider match scores below spotdl's threshold are
   retried once with the similarity filter relaxed. Matching stays strict for
   everything that already resolves.

4. The search box no longer freezes the interface. `get_search_results` blocks
   and was called straight from an `async def` handler, so the single uvicorn
   event loop stalled for as long as Spotify took - minutes - and every page in
   the UI went unreachable. It now runs in a worker thread. The url branch of
   the same handler gains the missing `return`, so a download is not followed by
   a pointless Spotify search for the url string and an empty result list being
   patched into a page the browser has already left.

5. Pasting an album or playlist url into the search box now downloads it. The
   handler yields a redirect to /downloads and then awaits the download inside
   the same request - but obeying a redirect means navigating, and navigating
   aborts the request, cancelling the download mid-flight. The release resolves,
   the log stops at "Download requested", and the queue stays empty forever with
   no error anywhere. The download runs as an independent task now, which is how
   the downloads page sees it anyway: it polls the client's progress tracker.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

VERSION = "VERSION_PLACEHOLDER"

RULES: dict[str, list[tuple[str, str]]] = {}
'''

FOOTER = '''

def apply(path: Path, rules: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """Apply `rules` to `path`. Returns (applied_count, list_of_problems)."""

    text = path.read_text()
    problems: list[str] = []
    applied = 0

    for number, (anchor, replacement) in enumerate(rules, start=1):
        # Test "already applied" before the anchor: an insertion deliberately
        # borrows context as its anchor, and that anchor survives the edit - so
        # checking the anchor first would re-apply the same insertion forever.
        if replacement in text:
            continue

        hits = text.count(anchor)
        if hits == 1:
            text = text.replace(anchor, replacement, 1)
            applied += 1
            continue
        if hits == 0:
            problems.append(
                f"{path.name}: rule {number} anchor is not present - upstream "
                f"code has drifted. Re-derive the patch with dev/gen_patch.py."
            )
            continue
        problems.append(
            f"{path.name}: rule {number} anchor is ambiguous ({hits} matches)"
        )

    if applied:
        path.write_text(text)
    return applied, problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--spotdl-dir",
        type=Path,
        default=Path("/app/spotdl"),
        help="location of the spotdl package inside the image",
    )
    args = parser.parse_args()

    if not args.spotdl_dir.is_dir():
        print(f"error: {args.spotdl_dir} is not a directory", file=sys.stderr)
        return 2

    failed = False
    for name, rules in RULES.items():
        path = args.spotdl_dir / name
        if not path.is_file():
            print(f"error: {path} not found", file=sys.stderr)
            failed = True
            continue

        applied, problems = apply(path, rules)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            failed = True
            continue
        print(f"{name}: {'patched' if applied else 'already patched'} "
              f"({applied}/{len(rules)} rules)")

    if failed:
        print(f"spotdl-webui-fixed {VERSION}: patch FAILED - do not ship this",
              file=sys.stderr)
        return 1

    print(f"spotdl-webui-fixed {VERSION}: patch OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="copy upstream source into dev/work/")
    extract.add_argument("--base", default=DEFAULT_BASE)
    extract.add_argument("--from-dir", help="read source from DIR instead of docker")
    extract.set_defaults(func=cmd_extract)

    sub.add_parser("generate", help="emit patches/patch_webui.py").set_defaults(
        func=cmd_generate
    )

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
