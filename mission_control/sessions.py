"""Reads the Claude Code session store.

Two rules learned the hard way and encoded here:

*   Never decode a slug back into a path. The encoding maps ``/``, ``_`` and ``.``
    all onto ``-``, so it is lossy and ambiguous. Encode forwards from a known
    path instead, and recover unknown paths from the ``cwd`` field inside a
    transcript.
*   Compare paths case-insensitively. macOS is case-insensitive, so
    ``Desktop/Projects`` and ``Desktop/projects`` are one directory with two
    slugs.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

STORE = Path.home() / ".claude" / "projects"
CACHE = Path.home() / ".cache" / "mission-control" / "index.json"


def slug(path: str | Path) -> str:
    """Encode an absolute path the way Claude Code names its session dirs."""
    return re.sub(r"[^a-zA-Z0-9-]", "-", str(path))


@dataclass
class Session:
    file: Path
    session_id: str
    title: str | None = None
    started: datetime | None = None
    ended: datetime | None = None
    prompts: int = 0
    edits: Counter = field(default_factory=Counter)  # abs file path -> n


@dataclass
class LiveState:
    state: str                 # "working" | "needs-you" | "idle"
    since: float | None        # seconds since the last message, if known

    @property
    def live(self) -> bool:
        return self.state != "idle"


def _tail_entries(path: Path, window: int = 96_000) -> list[dict]:
    """Parse JSON objects from the last `window` bytes of a transcript.

    The first line is usually a fragment, so it is dropped. Cheap enough to
    poll: bounded work regardless of how large the file has grown.
    """
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > window:
            fh.seek(size - window)
            fh.readline()
        raw = fh.read()
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _parse(path: Path) -> Session:
    s = Session(file=path, session_id=path.stem)
    ai = custom = None
    for line in path.open(errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue  # schema drifts between versions; skip rather than crash
        t = e.get("type")
        if t == "ai-title":
            ai = e.get("aiTitle")
        elif t == "custom-title":
            custom = e.get("customTitle")
        ts = e.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
            except ValueError:
                dt = None
            if dt:
                s.started = s.started or dt
                s.ended = dt
        if t == "user" and not e.get("isSidechain"):
            if isinstance(e.get("message", {}).get("content"), str):
                s.prompts += 1
        elif t == "assistant":
            for b in e.get("message", {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    if b.get("name") in ("Edit", "Write", "NotebookEdit"):
                        fp = (b.get("input") or {}).get("file_path")
                        if fp:
                            s.edits[fp] += 1
    s.title = custom or ai
    return s


class Store:
    """Indexes the session store, parsing lazily and caching by mtime."""

    def __init__(self, days: int = 30):
        self.days = days
        self._cache: dict[str, dict] = {}
        if CACHE.exists():
            try:
                self._cache = json.loads(CACHE.read_text())
            except Exception:
                self._cache = {}

    # -- discovery ---------------------------------------------------------
    def slugs_for(self, path: str, aliases: list[str] | None = None) -> list[Path]:
        want = slug(os.path.expanduser(path)).lower()
        found = []
        for d in STORE.iterdir() if STORE.exists() else []:
            if not d.is_dir():
                continue
            if d.name.lower() == want or d.name in (aliases or []):
                found.append(d)
        return found

    def files_for(self, path: str, aliases: list[str] | None = None) -> list[Path]:
        return [f for d in self.slugs_for(path, aliases) for f in d.glob("*.jsonl")]

    # -- parsing -----------------------------------------------------------
    def session(self, f: Path) -> Session:
        st = f.stat()
        key = str(f)
        c = self._cache.get(key)
        if c and c["size"] == st.st_size and c["mtime"] == st.st_mtime:
            s = Session(file=f, session_id=f.stem, title=c["title"], prompts=c["prompts"])
            s.started = datetime.fromisoformat(c["started"]) if c["started"] else None
            s.ended = datetime.fromisoformat(c["ended"]) if c["ended"] else None
            s.edits = Counter(c["edits"])
            return s
        s = _parse(f)
        self._cache[key] = {
            "size": st.st_size, "mtime": st.st_mtime, "title": s.title,
            "prompts": s.prompts, "edits": dict(s.edits),
            "started": s.started.isoformat() if s.started else None,
            "ended": s.ended.isoformat() if s.ended else None,
        }
        return s

    def flush(self) -> None:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(self._cache))

    # -- queries -----------------------------------------------------------
    def last_touched(self, path: str, aliases=None) -> float | None:
        """Seconds since the most recent session write. Uses mtime only — no
        parsing, so this stays cheap enough for every row on every render."""
        files = self.files_for(path, aliases)
        if not files:
            return None
        return datetime.now().timestamp() - max(f.stat().st_mtime for f in files)

    def week(self, path: str, aliases=None, days: int = 7) -> list[int]:
        """Sessions active per day, oldest first, ending today."""
        today = datetime.now().date()
        start = today - timedelta(days=days - 1)
        buckets = Counter()
        for f in self.files_for(path, aliases):
            if datetime.fromtimestamp(f.stat().st_mtime).date() < start:
                continue  # mtime prefilter: skip the bulk of a 44MB store
            s = self.session(f)
            if s.started and s.ended:
                d = s.started.date()
                while d <= s.ended.date():
                    if start <= d <= today:
                        buckets[d] += 1
                    d += timedelta(days=1)
        return [buckets.get(start + timedelta(days=i), 0) for i in range(days)]

    def recent(self, path: str, aliases=None, limit: int = 3) -> list[Session]:
        files = sorted(self.files_for(path, aliases),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        return [self.session(f) for f in files]

    # -- live state --------------------------------------------------------
    def live_state(self, path: str, aliases=None, idle_after: int = 90) -> LiveState:
        """Is a session running here right now, and is it waiting on *you*?

        Read from the tail of the newest transcript rather than the whole file —
        these reach 13MB and this is polled on a timer.

        The distinction that matters: an assistant entry with
        ``stop_reason == "end_turn"`` and nothing after it means Claude has
        finished and is waiting for a human. Anything else recent means it is
        still mid-turn and you are free.

        Known limitation: a tool call awaiting a *permission prompt* is
        indistinguishable from one that is simply running slowly — both look
        like a pending ``tool_use`` with no result yet. Reported as "working".
        """
        files = self.files_for(path, aliases)
        if not files:
            return LiveState("idle", None)
        f = max(files, key=lambda x: x.stat().st_mtime)
        age = datetime.now().timestamp() - f.stat().st_mtime
        if age > idle_after:
            return LiveState("idle", None)

        last_assistant_end = False
        last_ts = None
        for e in _tail_entries(f):
            t = e.get("type")
            if t not in ("assistant", "user"):
                continue
            if e.get("timestamp"):
                last_ts = e["timestamp"]
            if t == "user":
                last_assistant_end = False       # a new prompt resumes work
            elif t == "assistant":
                last_assistant_end = (
                    e.get("message", {}).get("stop_reason") == "end_turn"
                )
        since = None
        if last_ts:
            try:
                dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00")).astimezone()
                since = datetime.now().astimezone().timestamp() - dt.timestamp()
            except ValueError:
                since = age
        return LiveState("needs-you" if last_assistant_end else "working", since)

    def recorded_cwd(self, d: Path) -> str | None:
        """The path a slug dir was created from. Slugs cannot be decoded, so this
        is the only reliable way back to a real path."""
        for f in d.glob("*.jsonl"):
            for line in f.open(errors="replace"):
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("cwd"):
                    return e["cwd"]
        return None

    def classify(self, known: list[str], ignored: list[str] | None = None
                 ) -> dict[str, list[tuple[Path, str | None]]]:
        """Bucket every slug dir. Only ``stranded`` needs action.

        ``nested`` matters more than it looks: a project can contain its own
        sub-repo with its own slug, and reporting it
        as unknown would nag forever about something already tracked.
        """
        tracked = {slug(os.path.expanduser(p)).lower() for p in known}
        skip = {slug(os.path.expanduser(p)).lower() for p in (ignored or [])}
        out: dict[str, list] = {"linked": [], "nested": [], "untracked": [],
                                "stranded": [], "ignored": []}
        for d in sorted(STORE.iterdir()) if STORE.exists() else []:
            if not d.is_dir():
                continue
            name = d.name.lower()
            if name in tracked:
                out["linked"].append((d, None))
                continue
            cwd = self.recorded_cwd(d)
            # Nesting is decided on the slug, not the recorded cwd: a sub-repo's
            # cwd is whatever it was when last written, which may predate a move.
            # Exact match only: ~/Desktop is not a project, but that must not
            # silently ignore every project that lives underneath it.
            if name in skip:
                out["ignored"].append((d, cwd))
            elif any(name.startswith(t + "-") for t in tracked):
                out["nested"].append((d, cwd))
            elif cwd and os.path.isdir(cwd):
                out["untracked"].append((d, cwd))
            else:
                out["stranded"].append((d, cwd))
        return out
