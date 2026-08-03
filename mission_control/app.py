"""Roster + detail screens, driven by progress.toml and the session store."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Digits, Static

from . import activity, checks, config
from .render import (
    AMBER, BG, BLUE, CYAN, DIM, FAINT, FG, GREEN, MAGENTA, MUTED, PANEL, RED,
    STATUS,
    TEAL, ago, bar, fit, heat, spark,
)
from .sessions import Store

ORDER = {"active": 0, "blocked": 1, "paused": 2, "done": 3,
         "archived": 4, "ignored": 5}


class Roster(Screen):
    BINDINGS = [
        Binding("j,down", "move(1)", "down"),
        Binding("k,up", "move(-1)", "up"),
        Binding("g,home", "goto(0)", "top", show=False),
        Binding("G,end", "goto(-1)", "bottom", show=False),
        Binding("enter", "open", "open"),
        Binding("o", "resume", "resume"),
        Binding("w", "resume(True)", "new window"),
        Binding("s", "set_status", "status"),
        Binding("x", "retire", "retire"),
        Binding("c", "run_checks", "run checks"),
        Binding("a", "toggle_all", "show all"),
        Binding("m", "checkpoint", "month"),
        Binding("r", "refresh", "refresh"),
    ]
    CSS = f"""
    Screen {{ background: {BG}; color: {FG}; }}
    #head {{ padding: 1 3 0 3; }}
    #rule {{ padding: 0 3 1 3; }}
    #list {{ padding: 0 3; height: 1fr; scrollbar-size-vertical: 0; }}
    .row  {{ margin-bottom: 1; padding: 0 1; }}
    .sel  {{ background: {PANEL}; }}
    #foot {{ padding: 1 3 1 3; background: {PANEL}; }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.cfg = config.load()
        self.store = Store()
        self.index = 0
        self.rows: list[config.Project] = []
        # Finished and parked work is still tracked, but showing it by default
        # buries the handful of projects actually in flight. `a` reveals it.
        self.show_all = False

    def compose(self) -> ComposeResult:
        yield Static("", id="head")
        yield Static(f"[{FAINT}]{'━' * 68}[/]", id="rule")
        # can_focus=False: otherwise the container eats up/down as scroll
        # keys and the selection never moves. Mouse wheel still works.
        yield VerticalScroll(id="list", can_focus=False)
        yield Static("", id="foot")

    def on_mount(self) -> None:
        self.refresh_data()
        # Live state is the one thing on screen that changes without you doing
        # anything, so it gets a timer. Repaint only — remounting 14 rows every
        # few seconds would flicker and fight the scroll position.
        self.set_interval(4.0, self._repaint)

    # -- data --------------------------------------------------------------
    def refresh_data(self) -> None:
        """Full rebuild: config reloaded, row widgets remounted."""
        self.cfg = config.load()
        # paused work is still yours — it belongs on the roster, just
        # visually quieter. Only finished/filed work hides behind `a`.
        live = {"active", "blocked", "paused"}
        candidates = [p for p in self.cfg.projects if p.visible]
        self.hidden_count = sum(1 for p in candidates if p.status not in live)
        self.rows = sorted(
            (p for p in candidates if self.show_all or p.status in live),
            key=lambda p: (ORDER.get(p.status, 9), p.name.lower()),
        )
        self.index = min(self.index, max(len(self.rows) - 1, 0))
        lst = self.query_one("#list")
        lst.remove_children()
        # Floor the scale: with a real peak of 1, a single session would render
        # as a full block and read as a busy week.
        self.peak = max(4, max((max(self.store.week(str(p.path), p.aliases), default=0)
                                for p in self.rows), default=0))
        self.row_widgets = [
            Static(self._row(p, i == self.index),
                   classes="row sel" if i == self.index else "row")
            for i, p in enumerate(self.rows)
        ]
        for w in self.row_widgets:
            lst.mount(w)
        self._chrome()
        self.store.flush()

    def _repaint(self, scroll: bool = False) -> None:
        """Update row text in place, without touching the widget tree."""
        widgets = getattr(self, "row_widgets", [])
        for i, (p, w) in enumerate(zip(self.rows, widgets)):
            w.update(self._row(p, i == self.index))
            w.set_class(i == self.index, "sel")
        if scroll and 0 <= self.index < len(widgets):
            widgets[self.index].scroll_visible(animate=False)
        self._chrome()

    def _row(self, p: config.Project, sel: bool) -> str:
        stranded = not p.exists
        status = "stranded" if stranded else p.status
        colour, dot = STATUS.get(status, (DIM, "○"))
        ok, total, pct = checks.progress(p)
        touched = self.store.last_touched(str(p.path), p.aliases)
        wk = self.store.week(str(p.path), p.aliases)

        namec = FG if status in ("active", "blocked", "stranded") else DIM
        edge = f"[{colour}]▎[/]" if sel else " "
        # No checks defined is not 0% done — say nothing rather than something false.
        if total:
            meter = f"{bar(pct)} [b {colour}]{round(pct):>3}%[/]"
        else:
            meter = f"[{FAINT}]{'╌' * 16}[/] [{FAINT}]  —[/]"
        line1 = (f"{edge}[{colour}]{dot}[/] [b {namec}]{fit(p.name, 22)}[/] "
                 f"{meter}   {spark(wk, hi=getattr(self, 'peak', 0))}")
        cnt = f"{ok}/{total}" if total else "—"
        # Live state outranks every other note: it is the only thing here that
        # is true *right now* rather than true since the last time you looked.
        live = self.store.live_state(str(p.path), p.aliases)
        note = ""
        if live.state == "needs-you":
            note = f"   [b {AMBER}]◆ needs you[/]"
        elif live.state == "working":
            mins = int((live.since or 0) // 60)
            elapsed = f" {mins}m" if mins else ""
            note = f"   [{CYAN}]◆ working{elapsed}[/]"
        elif stranded:
            note = f"   [{AMBER}]path missing[/]"
        elif p.status == "blocked":
            note = f"   [{MAGENTA}]blocked[/]"
        elif p.status == "paused":
            note = f"   [{MUTED}]paused[/]"
        line2 = (
            f"   [{DIM}]{fit(p.phase or p.status, 20)}[/]"
            f"[{MUTED}]{cnt:>5} checks[/][{DIM}]{ago(touched):>11}[/]{note}"
        )
        if not sel:
            return f"{line1}\n{line2}"
        return "\n".join([line1, line2] + self._preview(p))

    def _preview(self, p: config.Project) -> list[str]:
        """Extra lines for the highlighted row.

        The point of the roster is deciding what to touch next, and that
        decision needs the *next unmet check* far more than it needs a
        percentage. Only computed for one row, so the git call per repaint is
        affordable.
        """
        w = max((self.size.width or 76) - 10, 40)
        out: list[str] = []
        if p.desc:
            out.append(f"     [{MUTED}]{fit(p.desc, w)}[/]")

        nxt = next((c for c in p.checks
                    if checks.is_fast(c) and checks.evaluate_fast(p, c) is False), None)
        if nxt is None:
            slow = checks.cached(p)
            nxt = next((c for c in p.checks
                        if not checks.is_fast(c) and slow.get(c.name) is False), None)
        if nxt:
            detail = nxt.value or "mark done by hand"
            room = max(w - (5 + 6 + len(nxt.name) + 3), 16)
            out.append(f"     [{RED}]next[/]  [{FG}]{nxt.name}[/]   "
                       f"[{DIM}]{fit(detail, room)}[/]")
        elif p.checks:
            out.append(f"     [{GREEN}]all checks passing[/]")
        else:
            out.append(f"     [{MUTED}]no checks — run /init-project here[/]")

        repo = next((r for r in (p.repos or [p.path]) if r.is_dir()), None)
        lc = activity.last_commit(repo) if repo else None
        if lc:
            # width has to account for every visible run on the line, not a
            # guess: indent + label + sha + gaps + the right-hand timestamp.
            room = max(w - (5 + 6 + len(lc.sha) + 2 + len(lc.when) + 2), 12)
            out.append(f"     [{MUTED}]last[/]  [{BLUE}]{lc.sha}[/]  "
                       f"[{DIM}]{fit(lc.subject, room)}[/]  [{MUTED}]{lc.when}[/]")

        recent = self.store.recent(str(p.path), p.aliases, limit=1)
        if recent and recent[0].ended:
            s0 = recent[0]
            tail = f"{s0.prompts}p · {sum(s0.edits.values())}e"
            room = max(w - (5 + 9 + len(tail) + 2), 12)
            out.append(f"     [{MUTED}]session[/]  "
                       f"[{DIM}]{fit(s0.title or '(untitled)', room)}[/]  "
                       f"[{MUTED}]{tail}[/]")
        return out

    def _chrome(self) -> None:
        n = len(self.rows)
        act = sum(1 for p in self.rows if p.status == "active")
        self.query_one("#head", Static).update(
            f"[b {FG}]MISSION[/][b {CYAN}] CONTROL[/]"
            f"[{DIM}]{'':<22}{act} active  ·  "
            f"{datetime.now():%a %d %b %H:%M}[/]"
        )
        # today's recap, attributed by file path rather than by slug
        today = datetime.now().date()
        touched: dict[str, int] = {}
        for p in self.rows:
            for f in self.store.files_for(str(p.path), p.aliases):
                if datetime.fromtimestamp(f.stat().st_mtime).date() != today:
                    continue
                s = self.store.session(f)
                for fp, k in s.edits.items():
                    for q in self.rows:
                        if fp.startswith(str(q.path)):
                            touched[q.name] = touched.get(q.name, 0) + k
                            break
        line = "  ".join(
            f"[{FG}]{k}[/][{MUTED}] {v}e[/]" for k, v in
            sorted(touched.items(), key=lambda kv: -kv[1])[:4]
        ) or f"[{DIM}]nothing yet[/]"
        stranded = sum(1 for p in self.rows if not p.exists)
        orph = len(self.store.classify(self.cfg.paths, self.cfg.ignore)['stranded'])
        health = []
        if stranded:
            health.append(f"[{AMBER}]▲ {stranded} missing[/]")
        if orph:
            health.append(f"[{AMBER}]▲ {orph} unknown slugs[/]")
        health.append(f"[{TEAL}]● {act} active[/]")
        hidden = getattr(self, "hidden_count", 0)
        if hidden and not self.show_all:
            health.append(f"[{MUTED}]+{hidden} done/parked — a[/]")
        elif self.show_all:
            health.append(f"[{MUTED}]showing all — a[/]")
        self.query_one("#foot", Static).update(
            f"[{DIM}]TODAY[/]    {line}\n\n" + "   ".join(health) +
            f"\n\n[{DIM}]⏎ open   o resume   c checks   s status   a all   "
            f"m month   x retire   q quit[/]"
        )

    # -- actions -----------------------------------------------------------
    def action_move(self, delta: int) -> None:
        if not self.rows:
            return
        self.index = (self.index + delta) % len(self.rows)
        self._repaint(scroll=True)

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.index = 0
        self.refresh_data()

    def action_goto(self, where: int) -> None:
        if self.rows:
            self.index = 0 if where == 0 else len(self.rows) - 1
            self._repaint(scroll=True)

    def action_open(self) -> None:
        if self.rows:
            self.app.push_screen(Detail(self.rows[self.index], self.store, self.index,
                                        len(self.rows)))

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_run_checks(self) -> None:
        """Evaluate the slow checks for the selected project.

        On a worker: cmd checks shell out and gh_pr hits the network, so this
        must never happen on a render path. Results are cached, which is what
        lets the roster show a real number for a project whose checks are
        mostly cmd rather than a permanent zero.
        """
        if not self.rows:
            return
        p = self.rows[self.index]
        slow = [c for c in p.checks if not checks.is_fast(c)]
        if not slow:
            self.notify(f"{p.name}: no cmd/gh_pr checks to run")
            return
        self.notify(f"running {len(slow)} check(s) for {p.name}…")
        self.run_worker(lambda: checks.run_all_slow(p), thread=True,
                        name=f"checks:{p.name}")

    def on_worker_state_changed(self, event) -> None:
        from textual.worker import WorkerState
        if event.state is WorkerState.SUCCESS and str(event.worker.name).startswith("checks:"):
            res = event.worker.result or {}
            passed = sum(1 for v in res.values() if v)
            self.notify(f"{passed}/{len(res)} passing")
            self._repaint()

    def action_checkpoint(self) -> None:
        from .modals import Checkpoint
        self.app.push_screen(Checkpoint(self.cfg, self.store))

    def action_set_status(self) -> None:
        """Change status without touching sessions — the other half of `x`."""
        from .modals import StatusPicker
        if not self.rows:
            return
        p = self.rows[self.index]

        def chosen(status: str | None) -> None:
            if not status or status == p.status:
                return
            self.cfg.set_status(p.name, status)
            config.save(self.cfg)
            self.notify(f"{p.name} → {status}")
            # It may have just left the default view; keep the cursor in range.
            self.refresh_data()

        self.app.push_screen(StatusPicker(p), chosen)

    def action_retire(self) -> None:
        """Drop a finished project out of --resume. Moves, never deletes."""
        from . import reconcile
        from .modals import Confirm
        if not self.rows:
            return
        p = self.rows[self.index]
        dirs = reconcile.slugs_under(p.path)

        if any(reconcile.is_live(d) for d in dirs):
            self.notify(f"{p.name}: a session is still active — quit it first",
                        severity="error")
            return

        n = sum(len(list(d.glob("*.jsonl"))) for d in dirs)
        detail = (f"{n} session(s) → ~/.claude/archive/shipped/{p.name}/\n"
                  f"status becomes 'done'.\n\n"
                  f"Nothing is deleted — moving it back restores it.")

        def done(ok: bool | None) -> None:
            if not ok:
                return
            good, log = reconcile.retire(p.name, dirs)
            self.cfg.set_status(p.name, "done")
            config.save(self.cfg)
            self.notify(f"retired {p.name}" if good else "; ".join(log),
                        severity="information" if good else "error")
            self.refresh_data()

        self.app.push_screen(Confirm(f"Retire {p.name}?", detail), done)

    def action_resume(self, new_window: bool = False) -> None:
        """Launch the project, then show its detail.

        Once you have picked something to work on, the roster's job is done and
        the pane should be that project's dashboard — its checks, commits and
        sessions — rather than the list you just chose from. `esc` goes back.
        """
        if not self.rows:
            return
        if resume(self, self.rows[self.index], self.store, new_window):
            self.action_open()


def resume(screen, p, store, new_window: bool = False) -> bool:
    """Start or resume a project in the other tmux pane.

    Shared by both screens. Never raises into the UI — every failure becomes a
    notification, because the failure modes here are environmental (no tmux, a
    single pane, a busy neighbour) rather than bugs.
    """
    from . import launcher

    if not p.path.is_dir():
        screen.notify(f"{p.name}: path missing", severity="error")
        return False

    recent = store.recent(str(p.path), p.aliases, limit=1)
    sid = recent[0].session_id if recent else None
    cmd = launcher.resume_command(p.path, sid)
    verb = "resuming" if sid else "starting"

    if new_window:
        ok, msg = launcher.new_window(p.path, launcher.resume_argv(sid), name=p.name)
        screen.notify(f"{verb} {p.name} — {msg}" if ok else msg,
                      severity="information" if ok else "error")
        return ok

    target = launcher.resolve_target()
    if target.usable:
        ok, msg = launcher.send(target, cmd)
        screen.notify(f"{verb} {p.name} in {target.pane}" if ok else msg,
                      severity="information" if ok else "error")
        return ok

    if target.can_split:
        # Alone in the window: build the layout rather than refusing. Work goes
        # to the left, the roster keeps the right.
        ok, msg = launcher.split_for_work(p.path, launcher.resume_argv(sid))
        screen.notify(f"{verb} {p.name} — {msg}" if ok else msg,
                      severity="information" if ok else "error")
        return ok

    # Busy or unresolvable: say why, put the command somewhere useful, and point
    # at the escape hatch rather than silently doing nothing.
    hint = "press w for a new window" if launcher.in_tmux() else "command copied"
    launcher.copy(cmd)
    screen.notify(f"{target.problem} — {hint}", severity="warning", timeout=8)
    return False


class Detail(Screen):
    """One project, in as much depth as the terminal has room for.

    Columns are sized from the actual screen width rather than a fixed 76, so
    running full-screen stops truncating things for no reason. j/k moves a
    cursor through the checks, commits and sessions; whatever is selected
    renders expanded, which is where the full commit subject, the files it
    touched, and the session's edited files live.
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("j,down", "move(1)", "down"),
        Binding("k,up", "move(-1)", "up"),
        Binding("enter", "resume", "resume"),
        Binding("w", "resume(True)", "new window"),
    ]
    CSS = f"""
    Screen  {{ background: {BG}; color: {FG}; }}
    #wrap   {{ padding: 1 3; }}
    #title  {{ padding-bottom: 1; }}
    #hero   {{ height: 5; }}
    Digits  {{ width: 16; color: {TEAL}; }}
    #nopct  {{ width: 16; }}
    #herotxt{{ padding: 1 0 0 1; width: 1fr; }}
    #body   {{ padding: 1 0 0 0; height: 1fr; }}
    #keys   {{ padding: 1 0 0 0; }}
    """

    def __init__(self, project, store, idx, total):
        super().__init__()
        self.p, self.store, self.idx, self.total = project, store, idx, total
        self.cursor = 0
        self.items: list[tuple[str, object]] = []

    # -- layout ------------------------------------------------------------
    @property
    def _w(self) -> int:
        """Usable text width. Falls back to 76 before the first layout pass."""
        return max((self.size.width or 76) - 8, 48)

    def compose(self) -> ComposeResult:
        p = self.p
        ok, total, pct = checks.progress(p)
        colour, dot = STATUS.get(p.status if p.exists else "stranded", (DIM, "○"))
        home = str(Path.home())
        shown = str(p.path).replace(home, "~")
        # Scrollable, and not focusable — otherwise the container eats
        # up/down for scrolling and the cursor stops moving.
        with VerticalScroll(id="wrap", can_focus=False):
            yield Static(
                f"[{MUTED}]◂ {self.idx + 1}/{self.total}[/]  [b {FG}]{p.name}[/]  "
                f"[{colour}]{dot} {p.status}[/][{DIM}]   {shown}[/]", id="title")
            with Horizontal(id="hero"):
                if total:
                    yield Digits(f"{round(pct)}")
                    right = (f"[b {TEAL}]%[/]  [b {CYAN}]{p.phase or p.status}[/]\n"
                             f"[{DIM}]   {ok} of {total} checks passing[/]\n"
                             f"   {bar(pct, 30)}")
                else:
                    yield Static(f"\n[{FAINT}]  ─────[/]", id="nopct")
                    right = (f"[b {CYAN}]{p.phase or p.status}[/]\n"
                             f"[{DIM}]   progress not measured yet[/]\n"
                             f"[{FAINT}]   run /init-project to define checks[/]")
                yield Static(right, id="herotxt")
            yield Static("", id="body")
            yield Static("", id="keys")

    def on_mount(self) -> None:
        self._collect()
        self._paint()

    def on_resize(self) -> None:
        self._paint()

    # -- data --------------------------------------------------------------
    def _collect(self) -> None:
        p = self.p
        self.items = [("check", c) for c in p.checks]
        for repo in (p.repos or [p.path]):
            if repo.is_dir():
                self.items += [("commit", c) for c in activity.recent_commits(repo, 5)]
                break
        self.items += [("session", s)
                       for s in self.store.recent(str(p.path), p.aliases, limit=5)
                       if s.ended]

    # -- rendering ---------------------------------------------------------
    def _check_lines(self, c, sel: bool) -> list[str]:
        r = checks.evaluate_fast(self.p, c) if checks.is_fast(c) else None
        if r is True:
            mark, mc = "✔", GREEN
        elif r is False:
            mark, mc = "✖", RED
        else:
            mark, mc = "○", MUTED
        w = self._w
        if not sel:
            room = max(w - 34, 20)
            val = c.value or ("done" if c.done else "not done")
            return [f"  [{mc}]{mark}[/] [{FG}]{fit(c.name, 20)}[/] "
                    f"[{MUTED}]{fit(c.type, 9)}[/][{DIM}]{fit(val, room)}[/]"]
        out = [f"  [{mc}]{mark}[/] [b {FG}]{c.name}[/]  [{MUTED}]{c.type}[/]"]
        if c.value:
            out.append(f"      [{DIM}]{c.value}[/]")
        if c.type == "manual":
            out.append(f"      [{DIM}]done = {str(c.done).lower()} "
                       f"— set by hand in progress.toml[/]")
        elif r is None:
            out.append(f"      [{MUTED}]not evaluated here: {c.type} checks shell out, "
                       f"so they run on demand[/]")
        else:
            out.append(f"      [{GREEN if r else RED}]"
                       f"{'passing' if r else 'not yet'}[/]")
        return out

    def _commit_lines(self, cm, sel: bool) -> list[str]:
        w = self._w
        if not sel:
            room = max(w - 30, 24)
            return [f"  [{BLUE}]▸[/] [{FG}]{cm.sha}[/]  {fit(cm.subject, room)}"
                    f"[{MUTED}]{cm.when}[/]"]
        out = [f"  [{BLUE}]▸[/] [b {FG}]{cm.sha}[/]  [{FG}]{cm.subject}[/]",
               f"      [{MUTED}]{cm.when} · {cm.files} file(s) "
               f"+{cm.added} −{cm.removed}[/]"]
        repo = next((r for r in (self.p.repos or [self.p.path]) if r.is_dir()), None)
        if repo:
            for f in activity.commit_files(repo, cm.sha):
                out.append(f"      [{DIM}]{fit(f, w - 8)}[/]")
        return out

    def _session_lines(self, s, sel: bool) -> list[str]:
        w = self._w
        edits = sum(s.edits.values())
        if not sel:
            room = max(w - 32, 24)
            return [f"  [{MUTED}]{s.ended:%a %H:%M}[/]  "
                    f"[{FG}]{fit(s.title or '(untitled)', room)}[/]"
                    f"[{MUTED}]{s.prompts}p · {edits}e[/]"]
        out = [f"  [{MUTED}]{s.ended:%a %d %b %H:%M}[/]  "
               f"[b {FG}]{s.title or '(untitled)'}[/]",
               f"      [{MUTED}]{s.started:%H:%M}–{s.ended:%H:%M} · "
               f"{s.prompts} prompts · {edits} edits[/]"]
        for path, n in s.edits.most_common(8):
            name = str(path).replace(str(self.p.path) + "/", "")
            out.append(f"      [{DIM}]{fit(name, w - 14)}[/][{MUTED}]×{n}[/]")
        return out

    def _paint(self) -> None:
        p, w = self.p, self._w
        lines: list[str] = []

        def section(label: str) -> None:
            if lines:
                lines.append("")
            lines.append(f"[{DIM}]{label}[/]")

        kinds = [k for k, _ in self.items]
        section("CHECKS")
        if "check" not in kinds:
            lines.append(f"  [{MUTED}]none defined — run /init-project[/]")
        nxt = None
        for i, (kind, obj) in enumerate(self.items):
            if kind != "check":
                continue
            lines += self._check_lines(obj, i == self.cursor)
            if (nxt is None and checks.is_fast(obj)
                    and checks.evaluate_fast(p, obj) is False):
                nxt = obj
        if nxt:
            lines.append(f"\n  [{RED}]▸[/] [b {FG}]next[/]  [{FG}]{nxt.name}[/]"
                         + (f" [{DIM}]— {nxt.value}[/]" if nxt.value else ""))
        if any(not checks.is_fast(c) for c in p.checks):
            at = checks.cached_at(p)
            when = f"last run {at:%a %H:%M}" if at else "never run"
            lines.append(f"  [{MUTED}]cmd/gh_pr checks: {when} — press c[/]")

        wk = self.store.week(str(p.path), p.aliases)
        cm_counts = activity.week_commits(p.repos)
        hi = max(4, max(wk, default=0), max(cm_counts, default=0))
        section("ACTIVITY")
        lines.append(f"             [{MUTED}]M T W T F S S[/]")
        lines.append(f"  [{DIM}]commits[/]    {heat(cm_counts)}   "
                     f"[{MUTED}]week[/] {spark(cm_counts, hi=hi)} "
                     f"[{FG}]{sum(cm_counts)}[/]")
        lines.append(f"  [{DIM}]sessions[/]   {heat(wk)}   "
                     f"[{MUTED}]week[/] {spark(wk, TEAL, hi=hi)} [{FG}]{sum(wk)}[/]")

        if "commit" in kinds:
            section("COMMITS")
            for i, (kind, obj) in enumerate(self.items):
                if kind == "commit":
                    lines += self._commit_lines(obj, i == self.cursor)

        if "session" in kinds:
            section("SESSIONS")
            for i, (kind, obj) in enumerate(self.items):
                if kind == "session":
                    lines += self._session_lines(obj, i == self.cursor)

        self.query_one("#body", Static).update("\n".join(lines))
        hint = (f"[{MUTED}]j/k expand a row   [/]" if self.items else "")
        self.query_one("#keys", Static).update(
            f"{hint}[{DIM}]⏎ resume   w new window   esc back   q quit[/]")

    # -- actions -----------------------------------------------------------
    def action_move(self, delta: int) -> None:
        if not self.items:
            return
        self.cursor = (self.cursor + delta) % len(self.items)
        self._paint()
        # keep the expanded row on screen when it grows past the fold
        body = self.query_one("#body")
        if self.cursor == 0:
            self.query_one("#wrap").scroll_home(animate=False)
        elif self.cursor == len(self.items) - 1:
            self.query_one("#wrap").scroll_end(animate=False)

    def action_resume(self, new_window: bool = False) -> None:
        resume(self, self.p, self.store, new_window)


class MissionControl(App):
    TITLE = "mission control"
    # Textual binds ctrl+c to a "press ctrl+q to quit" *hint* rather than to
    # quitting, and q meant "back" on sub-screens but "quit" on the roster —
    # so leaving took an unpredictable number of presses. One rule instead:
    # q and ctrl+c always quit; esc always goes back one screen.
    BINDINGS = [
        # Not priority: bindings bubble focused-widget -> screen -> app, so a
        # modal asking a question can still claim q as "cancel", while q quits
        # from every screen that does not.
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", show=False),
    ]

    def on_mount(self) -> None:
        self.push_screen(Roster())
