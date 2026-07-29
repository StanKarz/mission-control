"""The checks engine — progress is measured, never remembered.

Cheap types (``path``, ``git_tag``, ``manual``) evaluate inline during render.
Slow types (``cmd``, ``gh_pr``) are only ever evaluated from a worker via
:func:`run_slow`, and until then report ``None`` so the UI can grey them out.
Nothing here stores a percentage; it is recomputed from the predicates on
every open, which is what stops it drifting from reality.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from .config import Check, Project

FAST = ("path", "git_tag", "manual")
SLOW = ("cmd", "gh_pr")

CACHE = Path.home() / ".cache" / "mission-control" / "checks.json"


# ── cached slow results ──────────────────────────────────────────────────
def _load() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            pass
    return {}


def cached(project: Project) -> dict[str, bool | None]:
    """Last known result per check name. Absent means never run — which is
    reported as unresolved, never as failing."""
    block = _load().get(project.name, {}).get("results", {})
    return {k: v for k, v in block.items()}


def cached_at(project: Project) -> datetime | None:
    ts = _load().get(project.name, {}).get("at")
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


def record(project: Project, results: dict[str, bool | None]) -> None:
    data = _load()
    data[project.name] = {"at": datetime.now().isoformat(timespec="seconds"),
                          "results": results}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=1))


def run_all_slow(project: Project) -> dict[str, bool | None]:
    """Evaluate every slow check for a project and cache the outcome.

    Blocking and possibly networked — callers must put this on a worker or a
    CLI invocation, never in a render path.
    """
    results = {c.name: run_slow(project, c)
               for c in project.checks if not is_fast(c)}
    record(project, results)
    return results


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
    so the number never overstates; they are shown greyed rather than failed.

    Defaults to the cached slow results, so a project whose checks are mostly
    `cmd` still reports a real number between runs instead of a permanent zero.
    """
    slow = cached(project) if slow is None else slow
    total = len(project.checks)
    if not total:
        return 0, 0, 0.0
    passing = 0
    for c in project.checks:
        r = evaluate_fast(project, c) if is_fast(c) else slow.get(c.name)
        passing += bool(r)
    return passing, total, 100.0 * passing / total
