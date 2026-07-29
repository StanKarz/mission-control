"""Roster + detail screens, driven by progress.toml and the session store."""

from __future__ import annotations

from datetime import datetime

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

ORDER = {"active": 0, "blocked": 1, "done": 2, "archived": 3, "ignored": 4}


class Roster(Screen):
    BINDINGS = [
        Binding("j,down", "move(1)", "down"),
        Binding("k,up", "move(-1)", "up"),
        Binding("g,home", "goto(0)", "top", show=False),
        Binding("G,end", "goto(-1)", "bottom", show=False),
        Binding("enter", "open", "open"),
        Binding("o", "resume", "resume"),
        Binding("w", "resume(True)", "new window"),
        Binding("x", "retire", "retire"),
        Binding("c", "checkpoint", "checkpoint"),
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
        self.rows = sorted(
            (p for p in self.cfg.projects if p.visible),
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
        line2 = (
            f"   [{DIM}]{fit(p.phase or p.status, 20)}[/]"
            f"[{MUTED}]{cnt:>5} checks[/][{DIM}]{ago(touched):>11}[/]{note}"
        )
        return f"{line1}\n{line2}"

    def _chrome(self) -> None:
        n = len(self.rows)
        act = sum(1 for p in self.rows if p.status == "active")
        self.query_one("#head", Static).update(
            f"[b {FG}]MISSION[/][b {CYAN}] CONTROL[/]"
            f"[{DIM}]{'':<22}{act}/{n} active  ·  "
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
        self.query_one("#foot", Static).update(
            f"[{DIM}]TODAY[/]    {line}\n\n" + "   ".join(health) +
            f"\n\n[{DIM}]⏎ open   o resume   w window   x retire   c checkpoint   q quit[/]"
        )

    # -- actions -----------------------------------------------------------
    def action_move(self, delta: int) -> None:
        if not self.rows:
            return
        self.index = (self.index + delta) % len(self.rows)
        self._repaint(scroll=True)

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

    def action_checkpoint(self) -> None:
        from .modals import Checkpoint
        self.app.push_screen(Checkpoint(self.cfg, self.store))

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
        if self.rows:
            resume(self, self.rows[self.index], self.store, new_window)


def resume(screen, p, store, new_window: bool = False) -> None:
    """Start or resume a project in the other tmux pane.

    Shared by both screens. Never raises into the UI — every failure becomes a
    notification, because the failure modes here are environmental (no tmux, a
    single pane, a busy neighbour) rather than bugs.
    """
    from . import launcher

    if not p.path.is_dir():
        screen.notify(f"{p.name}: path missing", severity="error")
        return

    recent = store.recent(str(p.path), p.aliases, limit=1)
    sid = recent[0].session_id if recent else None
    cmd = launcher.resume_command(p.path, sid)
    verb = "resuming" if sid else "starting"

    if new_window:
        ok, msg = launcher.new_window(p.path, launcher.resume_argv(sid), name=p.name)
        screen.notify(f"{verb} {p.name} — {msg}" if ok else msg,
                      severity="information" if ok else "error")
        return

    target = launcher.resolve_target()
    if target.usable:
        ok, msg = launcher.send(target, cmd)
        screen.notify(f"{verb} {p.name} in {target.pane}" if ok else msg,
                      severity="information" if ok else "error")
        return

    # Busy or unresolvable: say why, put the command somewhere useful, and point
    # at the escape hatch rather than silently doing nothing.
    hint = "press w for a new window" if launcher.in_tmux() else "command copied"
    launcher.copy(cmd)
    screen.notify(f"{target.problem} — {hint}", severity="warning", timeout=8)


class Detail(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
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
    #checks {{ padding: 1 0 0 0; }}
    #week   {{ padding: 1 0 0 0; }}
    #recent {{ padding: 1 0 0 0; }}
    #keys   {{ padding: 1 0 0 0; }}
    """

    def __init__(self, project, store, idx, total):
        super().__init__()
        self.p, self.store, self.idx, self.total = project, store, idx, total

    def action_resume(self, new_window: bool = False) -> None:
        resume(self, self.p, self.store, new_window)

    def compose(self) -> ComposeResult:
        p = self.p
        ok, total, pct = checks.progress(p)
        colour, dot = STATUS.get(p.status if p.exists else "stranded", (DIM, "○"))
        with Vertical(id="wrap"):
            yield Static(
                f"[{MUTED}]◂ {self.idx + 1}/{self.total}[/]  [b {FG}]{p.name}[/]  "
                f"[{colour}]{dot} {p.status}[/]"
                f"[{DIM}]   {str(p.path).replace(str(p.path.home()), '~')}[/]\n"
                f"[{FAINT}]{'━' * 68}[/]", id="title")

            with Horizontal(id="hero"):
                # With no checks there is no percentage to report — an unqualified
                # 0% would read as "nothing done" rather than "nothing measured".
                if total:
                    yield Digits(f"{round(pct)}")
                    right = (f"[b {TEAL}]%[/]  [b {CYAN}]{p.phase or p.status}[/]\n"
                             f"[{DIM}]   {ok} of {total} checks passing[/]\n"
                             f"   {bar(pct, 30)}")
                else:
                    yield Static(f"\n[{FAINT}]  ─────[/]", id="nopct")
                    right = (f"[b {CYAN}]{p.phase or p.status}[/]\n"
                             f"[{DIM}]   progress not measured yet[/]\n"
                             f"[{FAINT}]   define checks to get a percentage[/]")
                yield Static(right, id="herotxt")

            lines, nxt = [], None
            for c in p.checks:
                r = checks.evaluate_fast(p, c) if checks.is_fast(c) else None
                if r is True:
                    mark, mc, nc = "✔", GREEN, FG
                elif r is False:
                    mark, mc, nc = "✖", RED, FG
                    nxt = nxt or c
                else:
                    mark, mc, nc = "○", DIM, DIM
                val = c.value or ("done" if c.done else "not done")
                lines.append(f"  [{mc}]{mark}[/] [{nc}]{fit(c.name, 18)}[/] "
                             f"[{MUTED}]{fit(c.type, 8)}[/][{DIM}]{val}[/]")
            body = "\n".join(lines) or f"  [{DIM}]no checks defined — run /init-project[/]"
            if nxt:
                body += (f"\n\n  [{RED}]▸[/] [b {FG}]next[/]  [{FG}]{nxt.name}[/] "
                         f"[{DIM}]— {nxt.value}[/]")
            yield Static(f"[{DIM}]CHECKS[/]\n{body}", id="checks")

            wk = self.store.week(str(p.path), p.aliases)
            cm = activity.week_commits(p.repos)
            lc = activity.last_commit(p.path)
            # Floor the scale, as the roster does. Without it a week of one
            # session a day is seven equal values, every bar renders at max,
            # and a quiet week is indistinguishable from a frantic one.
            hi = max(4, max(wk, default=0), max(cm, default=0))
            week = (f"[{DIM}]ACTIVITY[/]     [{MUTED}]M T W T F S S[/]\n"
                    f"[{DIM}]  commits[/]     {heat(cm)}    "
                    f"[{MUTED}]week[/] {spark(cm, hi=hi)} [{FG}]{sum(cm)}[/]\n"
                    f"[{DIM}] sessions[/]     {heat(wk)}    "
                    f"[{MUTED}]week[/] {spark(wk, TEAL, hi=hi)} [{FG}]{sum(wk)}[/]")
            if lc:
                week += (f"\n\n  [{BLUE}]▸[/] [{FG}]{lc.sha}[/]  {fit(lc.subject, 34)}"
                         f"[{DIM}]{lc.when:>12}[/]\n"
                         f"    [{MUTED}]{lc.files} files  +{lc.added} −{lc.removed}[/]")
            yield Static(week, id="week")

            recents = self.store.recent(str(p.path), p.aliases)
            rl = "\n".join(
                f"  [{MUTED}]{s.ended:%a %H:%M}[/]  [{FG}]{fit(s.title or '(untitled)', 34)}[/]"
                f"[{MUTED}]{s.prompts:>4}p · {sum(s.edits.values())}e[/]"
                for s in recents if s.ended
            ) or f"  [{DIM}]no sessions yet[/]"
            yield Static(f"[{DIM}]RECENT SESSIONS[/]\n{rl}", id="recent")
            yield Static(
                f"[{DIM}]⏎ resume in left pane   w new window   esc back   q quit[/]",
                id="keys")


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
