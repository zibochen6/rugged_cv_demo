#!/usr/bin/env python3
"""Verify that every path README.md references still exists in the tree.

Keeps the README honest after refactors and cleanups: a documented script, config
or doc that no longer exists would otherwise rot silently.

Resolution rules:
  * an exact repo-relative path must exist;
  * a bare basename mentioned in prose resolves against the whole tree;
  * glob patterns, placeholders, absolute/API paths and runtime-generated files
    are skipped.

Usage:  .venv/bin/python scripts/check_readme_paths.py [README.md]
Exit:   0 = all references resolve, 1 = at least one does not.
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".trellis",
             ".claude", ".codex", ".agents", "dist"}

# Created on demand at runtime (documented in README, not committed).
RUNTIME_ON_DEMAND = {
    "logs/tegrastats.log",
    "configs/_runtime_overrides.yaml",
    "logs/hub_events.jsonl",
    "logs/dms_events.jsonl",
}

# Prose that merely looks path-like (extend as needed; the top-level-dir rule in
# looks_like_repo_path() already filters most of it).
PROSE_IGNORE = {
    "8000/api/hub/status", "8080/8010", "CUDA/torch", "SAFE/WARNING/DANGER/SYSTEM_ERROR",
    "system/camera/hub/recording/segment", "venv/bin/python", "usb:0",
}

CODE_EXT = (".sh", ".py", ".yaml", ".json", ".md", ".ini", ".logrotate", ".service",
            ".desktop", ".sudoers", ".task", ".pt", ".log", ".jsonl", ".csv", ".jpg",
            ".ts", ".tsx", ".css", ".html")

TOKEN = re.compile(
    r"(?<![\w/.-])("
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"|[A-Za-z0-9_-]+\.(?:sh|py|yaml|md|ini|logrotate|service|desktop|sudoers|task|pt|jsonl|json|log|csv|jpg|ts|tsx|css|html)(?![A-Za-z0-9])"
    r")"
)
GLOB = re.compile(r"[A-Za-z0-9_./*{}\[\]-]*[*{}\[\]][A-Za-z0-9_./*{}\[\]-]*")
# remove whole tokens containing an angle-bracket placeholder, e.g. logs/hub_<module>.log
ANGLE_TOKEN = re.compile(r"[A-Za-z0-9_./-]*<[^<>]*>[A-Za-z0-9_./-]*")


def basename_index() -> set:
    names = set()
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        names.update(files)
    return names


def top_level() -> set:
    """Names that may legitimately start a repo-relative path."""
    names = {f for f in os.listdir(REPO) if os.path.isdir(os.path.join(REPO, f))}
    names = {n for n in names if n not in SKIP_DIRS}
    names |= {f for f in os.listdir(REPO) if os.path.isfile(os.path.join(REPO, f))}
    return names


def looks_like_repo_path(token: str, top: set) -> bool:
    """A token is a repo path only if it starts at a real top-level entry or ends
    with a code extension. This keeps prose like "backends/filters/ROI" or
    "owner/repo" out of the check while still catching genuine paths."""
    if token.endswith(CODE_EXT):
        return True
    first = token.split("/", 1)[0]
    return first in top


def candidates(text: str) -> set:
    text = ANGLE_TOKEN.sub(" ", text)   # drop placeholders like logs/hub_<module>.log
    text = GLOB.sub(" ", text)          # drop globs like logs/*_events.jsonl
    found = set()
    top = top_level()
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if not link.startswith(("http://", "https://", "#")):
            found.add(link)
    for raw in text.splitlines():
        for tok in TOKEN.findall(raw):
            tok = tok.strip(".,;:()[]`\"'")
            if not tok or tok in PROSE_IGNORE:
                continue
            if tok.startswith(("/", "~", "http")) or "/api/" in tok:
                continue
            if "/" not in tok and not tok.endswith(CODE_EXT):
                continue
            if not looks_like_repo_path(tok, top):
                continue
            found.add(tok)
    return found


def main() -> int:
    readme = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "README.md")
    text = open(readme, encoding="utf-8").read()
    names = basename_index()

    unresolved, checked = [], 0
    for cand in sorted(candidates(text)):
        if cand in RUNTIME_ON_DEMAND or os.path.basename(cand) in {
                os.path.basename(p) for p in RUNTIME_ON_DEMAND}:
            continue
        checked += 1
        if os.path.exists(os.path.join(REPO, cand.rstrip("/"))):
            continue
        if os.path.basename(cand) in names:
            continue
        unresolved.append(cand)

    print("README references checked: %d" % checked)
    print("unresolved: %d" % len(unresolved))
    for item in unresolved:
        print("  UNRESOLVED: %s" % item)
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())