"""Confirmation and the monthly checkpoint panel."""

from __future__ import annotations

from datetime import date

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Static, TextArea

from .render import AMBER, BG, CYAN, DIM, FAINT, FG, GREEN, MUTED, PANEL, TEAL


class Confirm(ModalScreen[bool]):
    """Yes/no gate in front of anything that moves files."""

    # priority, so the app-level "q quits" cannot fire while a confirmation is
    # on screen — answering a question must never exit the app.
    BINDINGS = [
        Binding("y", "yes", "yes", priority=True),
        Binding("n,escape,q", "no", "no", priority=True),
    ]
    CSS = f"""
    Confirm {{ align: center middle; }}
    #box {{
        width: 60; height: auto; padding: 1 3;
        background: {PANEL}; border: round {AMBER};
    }}
    #q {{ padding-bottom: 1; }}
    """

    def __init__(self, question: str, detail: str = "", danger: str = AMBER):
        super().__init__()
        self.question, self.detail, self.danger = question, detail, danger

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(f"[b {self.danger}]{self.question}[/]", id="q")
            if self.detail:
                yield Static(f"[{DIM}]{self.detail}[/]")
            yield Static(f"\n[{DIM}]y confirm     n cancel[/]")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


def _period(today: date | None = None) -> str:
    d = today or date.today()
    return f"{d.year:04d}-{d.month:02d}"


def _days_left_in_month(today: date | None = None) -> int:
    d = today or date.today()
    if d.month == 12:
        nxt = date(d.year + 1, 1, 1)
    else:
        nxt = date(d.year, d.month + 1, 1)
    return (nxt - d).days


def is_open(today: date | None = None, window: int = 3) -> bool:
    """Editable only in the last few days of the month.

    The whole point of this panel is that it is inert for most of the month —
    it should feel like a thing you do at month end, not another box nagging
    for input every morning.
    """
    return _days_left_in_month(today) <= window


class Checkpoint(Screen):
    """The four monthly questions, and their most recent answers."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "back"),
        Binding("ctrl+s", "save", "save"),
        Binding("tab", "focus_next", "next", show=False),
    ]
    CSS = f"""
    Screen {{ background: {BG}; color: {FG}; }}
    #wrap {{ padding: 1 3; }}
    #title {{ padding-bottom: 1; }}
    .q {{ padding: 1 0 0 0; }}
    TextArea {{
        height: 4; border: round {FAINT}; background: {PANEL};
    }}
    TextArea:focus {{ border: round {TEAL}; }}
    #summary {{ padding: 1 0 0 0; }}
    #keys {{ padding: 1 0 0 0; }}
    """

    def __init__(self, cfg, store=None):
        super().__init__()
        self.cfg = cfg
        self.store = store
        self.period = _period()
        self.editable = is_open()
        self.areas: list[TextArea] = []

    def _summary(self) -> str:
        """The month, from data. Most of what you'd ask yourself at month end —
        what shipped, what was busiest, what went quiet — is already recorded,
        so it should be shown rather than asked."""
        if self.store is None:
            return ""
        from . import report
        start, end = report.month_bounds()
        rep = report.build(self.cfg, self.store, start, end, self.period)

        out = [f"[{DIM}]THIS MONTH[/]",
               f"  [{FG}]{rep.total_commits}[/][{DIM}] commits · [/]"
               f"[{FG}]{rep.total_sessions}[/][{DIM}] sessions · [/]"
               f"[{FG}]{len(rep.worked)}[/][{DIM}] project(s) touched[/]"]

        if rep.worked:
            out.append(f"\n[{DIM}]  most active[/]")
            for r in rep.worked[:3]:
                out.append(f"    [{FG}]{r.project.name:<24}[/]"
                           f"[{MUTED}]{len(r.commits)}c · {len(r.sessions)}s[/]")
        done = [r for r in rep.worked if r.checks_total and r.checks_passing == r.checks_total]
        wip = [r for r in rep.worked if r.checks_total and r.checks_passing < r.checks_total]
        if done:
            out.append(f"\n[{GREEN}]  finished[/]  " +
                       ", ".join(r.project.name for r in done))
        if wip:
            out.append(f"[{AMBER}]  unfinished[/]  " + ", ".join(
                f"{r.project.name} ({r.checks_passing}/{r.checks_total})" for r in wip))
        if rep.quiet:
            out.append(f"[{DIM}]  quiet[/]  " +
                       ", ".join(p.project.name for p in rep.quiet))
        return "\n".join(out)

    def compose(self) -> ComposeResult:
        qs = self.cfg.questions
        prior = self.cfg.answers_for(self.period)
        left = _days_left_in_month()

        with VerticalScroll(id="wrap"):
            state = (f"[{TEAL}]open — {left} day{'s' if left != 1 else ''} left "
                     f"this month[/]" if self.editable
                     else f"[{DIM}]closed — opens in the last 3 days of the month[/]")
            yield Static(
                f"[b {FG}]CHECKPOINT[/]  [{DIM}]{self.period}[/]   {state}\n"
                f"[{FAINT}]{'━' * 68}[/]", id="title")

            summary = self._summary()
            if summary:
                yield Static(summary, id="summary")

            if not qs:
                yield Static(
                    f"\n[{DIM}]No written questions configured — the numbers above "
                    f"are derived.[/]\n"
                    f"[{MUTED}]Add prompts to checkpoint_questions in progress.toml "
                    f"if you want to write anything down.[/]", classes="q")
                yield Static(f"\n[{DIM}]esc back     q quit[/]", id="keys")
                return

            for i, q in enumerate(qs):
                yield Static(f"[b {CYAN}]{i + 1}.[/] [{FG}]{q}[/]", classes="q")
                if self.editable:
                    ta = TextArea(prior[i] if i < len(prior) else "", id=f"a{i}")
                    self.areas.append(ta)
                    yield ta
                else:
                    ans = prior[i] if i < len(prior) else ""
                    yield Static(f"   [{DIM}]{ans or '— not answered —'}[/]")

            yield Static(
                f"[{DIM}]{'ctrl+s save     ' if self.editable else ''}esc back     q quit[/]",
                id="keys")

    def action_save(self) -> None:
        if not self.editable:
            self.notify("checkpoint is closed until month end", severity="warning")
            return
        answers = [a.text.strip() for a in self.areas]
        self.cfg.set_answers(self.period, answers)
        from . import config as _config
        _config.save(self.cfg)
        n = sum(1 for a in answers if a)
        self.notify(f"saved {n}/{len(answers)} answers for {self.period}")
