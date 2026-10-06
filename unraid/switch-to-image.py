#!/usr/bin/env python3
"""
Point the SpotDL-WebGUI Unraid template at the ghcr image.

Default is a dry run: prints every line it would change and writes nothing.
Add --apply to write, after taking a timestamped backup.

Edits
  Repository   -> ghcr.io/spaceinvaderone/spotdl-webui-fixed:latest
  Registry     -> https://ghcr.io
  ExtraParams  -> --user 99:100   (writes files as nobody:users, like everything
                   else on the array; the image supports any uid)
  remove       -> the PYTHONPATH variable, the routes.py / api.py patch mounts and
                   the CLI launcher mount. The image bakes all four, and leaving
                   the mounts in place would override the build-time patch and
                   defeat its drift check.
  remove       -> four template fields that were never wired to anything
                   (Output Format, Bitrate, Disable Bitrate Conversion, WebGUI
                   Host). They are Type="Variable" with targets such as
                   /etc/spotdl/config.json, so they only produce junk env vars.
                   Real settings live in the web UI's Settings page.
"""

import argparse
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

XML = Path("/boot/config/plugins/dockerMan/templates-user/my-SpotDL-WebGUI.xml")

REPOSITORY = "ghcr.io/spaceinvaderone/spotdl-webui-fixed:latest"
REGISTRY = "https://ghcr.io"
EXTRA_PARAMS = "--user 99:100"

# Substrings that identify a whole <Config ...> line to delete.
DROP = [
    'Target="PYTHONPATH"',
    'Target="/app/spotdl/web/routes.py"',
    'Target="/app/spotdl/web/api.py"',
    'Target="/usr/local/bin/spotdl"',
    'Name="Output Format"',
    'Name="Bitrate"',
    'Name="Disable Bitrate Conversion"',
    'Name="WebGUI Host"',
    'Name="Cookie File Path"',
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    if not XML.is_file():
        print(f"error: {XML} not found", file=sys.stderr)
        return 2

    original = XML.read_text()
    out = []
    removed = []

    for line in original.splitlines(keepends=True):
        hit = next((d for d in DROP if d in line), None)
        if hit:
            name = line.split('Name="', 1)[1].split('"', 1)[0] if 'Name="' in line else hit
            removed.append(name)
            continue

        stripped = line.strip()
        if stripped.startswith("<Repository>"):
            line = line.replace("</Repository>", "")
            line = f"  <Repository>{REPOSITORY}</Repository>\n"
        elif stripped.startswith("<Registry>"):
            line = f"  <Registry>{REGISTRY}</Registry>\n"
        elif stripped == "<ExtraParams />":
            line = f"  <ExtraParams>{EXTRA_PARAMS}</ExtraParams>\n"

        out.append(line)

    new = "".join(out)

    # Never write XML that will not parse - Unraid silently keeps the old
    # container config if the template is unreadable, which looks like Apply
    # doing nothing.
    try:
        root = ET.fromstring(new)
    except ET.ParseError as e:
        print(f"refusing to write: resulting XML does not parse ({e})", file=sys.stderr)
        return 1

    print(f"Repository  -> {REPOSITORY}")
    print(f"Registry    -> {REGISTRY}")
    print(f"ExtraParams -> {EXTRA_PARAMS}   (files land as nobody:users)")
    print(f"removing {len(removed)} entries:")
    for name in removed:
        print(f"   - {name}")

    kept = [
        c.get("Name")
        for c in root.iter("Config")
    ]
    print("keeping:", ", ".join(kept))

    if not args.apply:
        print("\ndry run - nothing written. re-run with --apply")
        return 0

    backup = XML.with_name(XML.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(XML, backup)
    XML.write_text(new)
    print(f"\napplied. backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
