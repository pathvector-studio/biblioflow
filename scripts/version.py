#!/usr/bin/env python3
"""VERSION is the one place the release version is set; this keeps every copy in step.

  scripts/version.py check            # every copy matches VERSION (CI)
  scripts/version.py check --tag v1.2.3   # ... and the tag is v$(cat VERSION) (CI on a tag push)
  scripts/version.py set 1.2.3        # write VERSION and every copy (see RELEASING.md)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.(0|[1-9]\d*))?$")
V = r"[0-9A-Za-z.+-]+"

# (file, pattern with one group for the version). Each pattern must match exactly once.
COPIES = [
    ("backend/pyproject.toml", rf'(?m)^version = "({V})"$'),
    ("backend/uv.lock", rf'name = "biblioflow-backend"\nversion = "({V})"'),
    ("backend/app/__init__.py", rf'__version__ = "({V})"'),
    ("mcp/pyproject.toml", rf'(?m)^version = "({V})"$'),
    ("mcp/uv.lock", rf'name = "biblioflow-mcp"\nversion = "({V})"'),
    ("frontend/package.json", rf'^\{{\n  "name": "[^"]+",\n(?:  "private": true,\n)?  "version": "({V})"'),
    ("frontend/package-lock.json", rf'^\{{\n  "name": "[^"]+",\n  "version": "({V})"'),
    ("frontend/package-lock.json", rf'"packages": \{{\n    "": \{{\n      "name": "[^"]+",\n      "version": "({V})"'),
    ("README.md", rf"\*\*Status: early preview \(v({V})\)\.\*\*"),
]


def found(path: str, pattern: str) -> tuple[str, re.Match[str]]:
    text = (ROOT / path).read_text(encoding="utf-8")
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        sys.exit(f"{path}: expected one version line, found {len(matches)} (update scripts/version.py)")
    return text, matches[0]


def check(tag: str | None) -> int:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    bad = [] if SEMVER.match(version) else [f"VERSION: {version!r} is not X.Y.Z or X.Y.Z-rc.N"]
    for path, pattern in COPIES:
        got = found(path, pattern)[1].group(1)
        if got != version:
            bad.append(f"{path}: {got} (VERSION is {version})")
    if tag is not None and tag != f"v{version}":
        bad.append(f"tag {tag} does not match VERSION {version} (tags are v$(cat VERSION))")
    # The JSON files must still parse after a rewrite.
    for path in ("frontend/package.json", "frontend/package-lock.json"):
        json.loads((ROOT / path).read_text(encoding="utf-8"))
    for line in bad:
        print(line, file=sys.stderr)
    if bad:
        print("\nRun scripts/version.py set <version> (see RELEASING.md).", file=sys.stderr)
    return 1 if bad else 0


def set_version(version: str) -> int:
    if not SEMVER.match(version):
        sys.exit(f"{version!r} is not X.Y.Z or X.Y.Z-rc.N (no leading v)")
    (ROOT / "VERSION").write_text(version + "\n", encoding="utf-8")
    for path, pattern in COPIES:
        text, m = found(path, pattern)
        (ROOT / path).write_text(text[: m.start(1)] + version + text[m.end(1) :], encoding="utf-8")
    print(f"version set to {version}")
    return check(None)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--tag")
    s = sub.add_parser("set")
    s.add_argument("version")
    a = ap.parse_args()
    return check(a.tag) if a.cmd == "check" else set_version(a.version)


if __name__ == "__main__":
    sys.exit(main())
