"""The checks engine — progress is measured, never remembered.

Cheap types (``path``, ``git_tag``, ``manual``) evaluate inline during render.
Slow types (``cmd``, ``gh_pr``) are only ever evaluated from a worker via
:func:`run_slow`, and until then report ``None`` so the UI can grey them out.
Nothing here stores a percentage; it is recomputed from the predicates on
every open, which is what stops it drifting from reality.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Check, Project

FAST = ("path", "git_tag", "manual")
SLOW = ("cmd", "gh_pr")


def is_fast(c: Check) -> bool:
    return c.type in FAST


def evaluate_fast(project: Project, c: Check) -> bool | None:
    """None means 'not determinable here' — never means 'failing'."""
    if c.type == "manual":
        return c.done
    if c.type == "path":
        return (project.path / c.value).exists()
    if c.type == "git_tag":
        try:
            r = subprocess.run(
                ["git", "-C", str(project.path), "tag", "--list", c.value],
                capture_output=True, text=True, timeout=5,
            )
            return bool(r.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def run_slow(project: Project, c: Check) -> bool | None:
    """Only ever called from a worker. `cmd` is arbitrary shell from the user's
    own config, so it runs with a timeout, in the project directory, and never
    implicitly — `mc doctor` does not evaluate it at all."""
    if c.type == "cmd":
        try:
            r = subprocess.run(
                c.value, shell=True, cwd=str(project.path),
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return None
    if c.type == "gh_pr":
        try:
            r = subprocess.run(
                ["gh", "pr", "view", c.value, "--json", "state", "-q", ".state"],
                capture_output=True, text=True, timeout=15,
            )
            return r.stdout.strip().upper() == "MERGED" if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def progress(project: Project, slow: dict[str, bool | None] | None = None) -> tuple[int, int, float]:
    """(passing, total, percent). Unresolved slow checks count as not-yet-passing
    so the number never overstates; they are shown greyed rather than failed."""
    slow = slow or {}
    total = len(project.checks)
    if not total:
        return 0, 0, 0.0
    passing = 0
    for c in project.checks:
        r = evaluate_fast(project, c) if is_fast(c) else slow.get(c.name)
        passing += bool(r)
    return passing, total, 100.0 * passing / total
