"""Period rollups: what happened across every project in a week or a month.

Answers the questions you'd otherwise try to remember — what got pushed, what
was worked on, which projects were busiest and which went quiet — from data
already on disk. Commit subjects come from git; session titles are the ones
Claude Code writes for itself. Nothing is synthesised or summarised by a model:
the commit message *is* the summary, written by whoever made the change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import activity, checks
from .config import Config, Project
from .sessions import Session, Store


def week_bounds(d: date | None = None, back: int = 0) -> tuple[date, date]:
    """Monday–Sunday containing `d`, optionally `back` weeks earlier."""
    d = (d or date.today()) - timedelta(weeks=back)
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


def month_bounds(d: date | None = None, back: int = 0) -> tuple[date, date]:
    d = d or date.today()
    y, m = d.year, d.month - back
    while m < 1:
        m += 12
        y -= 1
    first = date(y, m, 1)
    last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return first, last


@dataclass
class ProjectReport:
    project: Project
    commits: list[activity.Commit] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    edits: int = 0
    checks_passing: int = 0
    checks_total: int = 0

    @property
    def active(self) -> bool:
        return bool(self.commits or self.sessions)

    @property
    def score(self) -> tuple[int, int]:
        return (len(self.commits), len(self.sessions))


@dataclass
class Report:
    start: date
    end: date
    label: str
    projects: list[ProjectReport]

    @property
    def worked(self) -> list[ProjectReport]:
        return sorted((p for p in self.projects if p.active),
                      key=lambda r: r.score, reverse=True)

    @property
    def quiet(self) -> list[ProjectReport]:
        """Tracked, still live, but nothing happened. Retired work is excluded —
        a finished project being quiet is not a finding."""
        return [p for p in self.projects
                if not p.active and p.project.status in ("active", "blocked")]

    @property
    def total_commits(self) -> int:
        return sum(len(p.commits) for p in self.projects)

    @property
    def total_sessions(self) -> int:
        return sum(len(p.sessions) for p in self.projects)


def build(cfg: Config, store: Store, start: date, end: date, label: str) -> Report:
    out = []
    for p in cfg.projects:
        if not p.visible:
            continue
        r = ProjectReport(project=p)

        for repo in (p.repos or [p.path]):
            if repo.is_dir():
                r.commits.extend(activity.commits_between(repo, start, end))

        for f in store.files_for(str(p.path), p.aliases):
            # mtime only prefilters — a file touched by a move says nothing about
            # when work happened. The timestamps inside decide.
            from datetime import datetime
            if datetime.fromtimestamp(f.stat().st_mtime).date() < start:
                continue
            s = store.session(f)
            if s.ended and start <= s.ended.date() <= end:
                r.sessions.append(s)
                r.edits += sum(s.edits.values())

        if p.exists and p.checks:
            r.checks_passing, r.checks_total, _ = checks.progress(p)
        out.append(r)
    return Report(start, end, label, out)


def render(rep: Report, colour: bool = True) -> str:
    """Plain text, so it works piped into a file or a shell startup."""
    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if colour else s

    lines = [
        c("1", f"{rep.label}  {rep.start:%a %d %b} – {rep.end:%a %d %b %Y}"),
        c("2", f"{rep.total_commits} commits · {rep.total_sessions} sessions "
               f"across {len(rep.worked)} project(s)"),
    ]
    if not rep.worked:
        lines.append("\n" + c("2", "  nothing recorded in this period"))

    for r in rep.worked:
        lines.append("")
        head = f"▌ {r.project.name}"
        stat = f"{len(r.commits)} commits · {len(r.sessions)} sessions"
        if r.edits:
            stat += f" · {r.edits} edits"
        lines.append(f"{c('36', head)}  {c('2', stat)}")

        if r.commits:
            lines.append(c("2", "  pushed"))
            for cm in r.commits[:8]:
                lines.append(f"    {c('2', cm.sha)}  {cm.subject[:58]}")
            if len(r.commits) > 8:
                lines.append(c("2", f"    … {len(r.commits) - 8} more"))

        titles = [s.title for s in r.sessions if s.title]
        seen, uniq = set(), []
        for t in titles:                      # one session can be re-titled
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        if uniq:
            lines.append(c("2", "  worked on"))
            for t in uniq[:5]:
                lines.append(f"    {t[:62]}")

        if r.checks_total:
            state = "done" if r.checks_passing == r.checks_total else "in progress"
            lines.append(c("2", f"  checks {r.checks_passing}/{r.checks_total} — {state}"))

    if rep.quiet:
        lines.append("")
        names = ", ".join(p.project.name for p in rep.quiet)
        lines.append(c("2", f"quiet  {names}"))
    return "\n".join(lines)
