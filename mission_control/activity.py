"""git as the durable half of the recap.

Session transcripts say what was attempted; a commit says what was kept.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass
class Commit:
    sha: str
    subject: str
    when: str
    files: int = 0
    added: int = 0
    removed: int = 0


def _git(repo: Path, *args: str, timeout: int = 5) -> str | None:
    if not (repo / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def last_commit(repo: Path) -> Commit | None:
    out = _git(repo, "log", "-1", "--pretty=%h%x00%s%x00%ar", "--shortstat")
    if not out or "\x00" not in out:
        return None
    head, *rest = out.strip().splitlines()
    sha, subject, when = head.split("\x00")
    c = Commit(sha=sha, subject=subject, when=when)
    for line in rest:
        parts = line.replace(",", "").split()
        for i, tok in enumerate(parts):
            if tok.startswith("file"):
                c.files = int(parts[i - 1])
            elif tok.startswith("insertion"):
                c.added = int(parts[i - 1])
            elif tok.startswith("deletion"):
                c.removed = int(parts[i - 1])
    return c


def commits_between(repo: Path, since: date, until: date) -> list[Commit]:
    """Commits authored in [since, until]. `until` is inclusive, so the git
    boundary is pushed a day forward — `--until` is exclusive of the day's
    later hours otherwise, and month-end commits silently vanish."""
    out = _git(repo, "log",
               f"--since={since.isoformat()}",
               f"--until={(until + timedelta(days=1)).isoformat()}",
               "--pretty=%h%x00%s%x00%ar%x00%cs", timeout=10)
    if not out:
        return []
    commits = []
    for line in out.strip().splitlines():
        parts = line.split("\x00")
        if len(parts) >= 3:
            commits.append(Commit(sha=parts[0], subject=parts[1], when=parts[2]))
    return commits


def week_commits(repos: list[Path], days: int = 7) -> list[int]:
    """Commits per day, oldest first, ending today."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    counts: Counter[date] = Counter()
    for repo in repos:
        out = _git(repo, "log", f"--since={start.isoformat()}", "--pretty=%cs")
        if not out:
            continue
        for line in out.splitlines():
            try:
                counts[date.fromisoformat(line.strip())] += 1
            except ValueError:
                continue
    return [counts.get(start + timedelta(days=i), 0) for i in range(days)]
