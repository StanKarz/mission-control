"""Render the real screens to SVG, against demo data.

`mockup.py` also writes to `shots/`, but it is a hardcoded sketch from before
the build: its screenshots show a layout that no longer exists. These come out
of the actual app, so they go stale only when the app changes.

Demo projects rather than the real config, for two reasons: a screenshot of
someone's own roster publishes their whole project inventory, and real activity
is whatever happened to be true that day, which is rarely the thing worth
showing.

    uv run python shots.py          # writes shots/roster.svg, shots/detail.svg
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent / "shots"

# (name, status, phase, desc, [(check name, type, value, done)])
PROJECTS = [
    ("orbital-sim", "active", "phase 2 — baselines",
     "RL controller for a two-body docking task",
     [("baseline run", "path", "results/baseline.json", None),
      ("eval harness green", "cmd", "true", None),
      ("picked the reward shaping", "manual", None, True),
      ("writeup over 800 words", "path", "writeup.md", None)]),
    ("ascii-renderer", "active", "structure pass",
     "edge-aware ASCII renderer, stills first",
     [("gradient ramp chosen", "manual", None, True),
      ("renders a test frame", "path", "out/frame.txt", None),
      ("timing under 40ms", "cmd", "false", None)]),
    ("pgexercises-notes", "blocked", "awaiting review",
     "worked solutions, with the mistakes kept in",
     [("joins section drafted", "path", "joins.md", None),
      ("window functions drafted", "path", "windows.md", None)]),
    ("rally-cutter", "paused", "on ice",
     "cuts tennis footage into rallies on ball-contact audio",
     [("clip detector written", "path", "detect.py", None)]),
    ("harmonica-log", "done", "shipped", "", []),
]

# Which day of the past week each project was worked on, and how hard.
ACTIVITY = {
    "orbital-sim":       [(6, 14), (5, 9), (3, 22), (1, 31), (0, 18)],
    "ascii-renderer":    [(4, 6), (2, 17), (0, 9)],
    "pgexercises-notes": [(5, 11), (4, 4), (1, 8)],
    "rally-cutter":      [(6, 3)],
    "harmonica-log":     [],
}

# Cycled per session, so the detail screen does not list one title three times.
TITLES = {
    "orbital-sim": ["Baseline PPO run and eval harness",
                    "Reward shaping, third attempt",
                    "Seed sweep and the variance it exposed"],
    "ascii-renderer": ["Edge detection over the luminance ramp",
                       "Glyph ramp picked by hand",
                       "Downsampling without losing the edges"],
    "pgexercises-notes": ["Window function exercises 1-12",
                          "Joins section, worked solutions"],
    "rally-cutter": ["Audio onset detection spike"],
}

# (days ago, subject) per project, so the commits row has something in it.
COMMITS = {
    "orbital-sim": [(6, "env: fix the termination bound on the docking cone"),
                    (3, "eval: report per-seed return, not just the mean"),
                    (1, "baselines: log episode return per seed")],
    "ascii-renderer": [(4, "ramp: pick glyphs by coverage, not by ascii order"),
                       (0, "edges: keep the Sobel pass above the downsample")],
    "pgexercises-notes": [(5, "joins: add the self-join worked example"),
                          (1, "windows: rank vs dense_rank, with the tie case")],
    "rally-cutter": [],
}


def _entry(kind: str, when: datetime, **kw) -> str:
    return json.dumps({"type": kind,
                       "timestamp": when.astimezone(timezone.utc)
                                        .isoformat().replace("+00:00", "Z"),
                       **kw})


# Two of the demo projects have a session open, in the two states the roster
# distinguishes: mid-turn, and finished and waiting on you.
LIVE = {"ascii-renderer": "working", "orbital-sim": "needs-you"}


def _transcript(path: Path, project: Path, title: str, edits: int,
                when: datetime, live: str | None) -> None:
    """One session's worth of plausible jsonl."""
    lines = [json.dumps({"type": "ai-title", "aiTitle": title}),
             _entry("user", when, message={"content": "carry on from yesterday"})]
    for i in range(edits):
        lines.append(_entry(
            "assistant", when + timedelta(seconds=i * 7),
            message={"content": [{"type": "tool_use", "name": "Edit",
                                  "input": {"file_path": str(project / f"src/mod{i % 5}.py")}}]}))
    # A session reads as live only while its file is fresh. What it is doing
    # then comes from the last assistant entry: end_turn means it has finished
    # and is waiting on a human, anything else means it is still mid-turn.
    if live == "working":
        lines.append(_entry("assistant", datetime.now(),
                            message={"content": [{"type": "tool_use",
                                                  "name": "Bash", "input": {}}]}))
    elif live == "needs-you":
        lines.append(_entry("assistant", datetime.now(),
                            message={"stop_reason": "end_turn",
                                     "content": [{"type": "text",
                                                  "text": "which seed should I use?"}]}))
    path.write_text("\n".join(lines) + "\n")


def _repos(root: Path, now: datetime) -> None:
    """Give each project real git history, dated across the week."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "d@e",
           "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "d@e"}
    for name, commits in COMMITS.items():
        d = root / name
        if not commits:
            continue
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
        for i, (ago, subject) in enumerate(commits):
            (d / "src" / f"mod{i}.py").write_text(f"# {subject}\n")
            when = (now - timedelta(days=ago, hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True, env=env)
            subprocess.run(["git", "commit", "-q", "-m", subject], cwd=d, check=True,
                           env={**env, "GIT_AUTHOR_DATE": when,
                                "GIT_COMMITTER_DATE": when})


def build(tmp: Path) -> Path:
    """Lay out demo projects, a session store and a config. Returns the config."""
    import mission_control.checks as checks
    import mission_control.config as config
    import mission_control.sessions as sessions

    root = tmp / "projects"
    store = tmp / "store"
    store.mkdir(parents=True)
    sessions.STORE = store
    sessions.CACHE = tmp / "index.json"
    checks.CACHE = tmp / "checks.json"

    lines = ['[meta]', f'project_roots = ["{root}"]', 'ignore = []',
             'checkpoint_questions = ["What shipped?", "What stalled?"]', '']
    for name, status, phase, desc, items in PROJECTS:
        d = root / name
        (d / "src").mkdir(parents=True)
        lines += [f'[projects."{name}"]', f'path = "{d}"',
                  f'status = "{status}"', f'phase = "{phase}"']
        if desc:
            lines.append(f'desc = "{desc}"')
        for cname, ctype, value, done in items:
            lines += ['', f'  [[projects."{name}".checks]]',
                      f'  name = "{cname}"', f'  type = "{ctype}"']
            if ctype == "manual":
                lines.append(f'  done = {str(bool(done)).lower()}')
            else:
                lines.append(f'  value = "{value}"')
            # Make roughly the first half of each project's path checks pass.
            if ctype == "path" and value and "writeup" not in value \
                    and "windows" not in value and "timing" not in value:
                f = d / value
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("x\n")
        lines.append('')

    now = datetime.now()
    _repos(root, now)
    for name, days in ACTIVITY.items():
        d = root / name
        slug = store / sessions.slug(str(d))
        slug.mkdir(parents=True, exist_ok=True)
        for i, (ago, edits) in enumerate(days):
            when = now - timedelta(days=ago, hours=2)
            f = slug / f"{name}-{i}.jsonl"
            state = LIVE.get(name) if ago == 0 else None
            titles = TITLES.get(name) or [name]
            _transcript(f, d, titles[i % len(titles)], edits, when, state)
            if state is None:
                # Backdate so week() bucketises it, and so today's own sessions
                # read as finished rather than as a session still running.
                stamp = (when if ago else now - timedelta(hours=3)).timestamp()
                os.utime(f, (stamp, stamp))

    cfg = tmp / "progress.toml"
    cfg.write_text("\n".join(lines))
    os.environ["MC_CONFIG"] = str(cfg)
    config.PATH = cfg
    # The detail screen shortens a path against Path.home(), which reads $HOME.
    # Pointing it at the demo root turns a /var/folders/… temp path into
    # ~/projects/orbital-sim. Set last: the git calls above want the real one.
    os.environ["HOME"] = str(tmp)
    return cfg


async def shoot(tmp: Path) -> None:
    from mission_control.app import MissionControl

    OUT.mkdir(exist_ok=True)
    app = MissionControl()
    async with app.run_test(size=(96, 32)) as pilot:
        await pilot.pause()
        app.save_screenshot(str(OUT / "roster.svg"))
        print("wrote shots/roster.svg")
        await pilot.press("enter")
        await pilot.pause()
        app.save_screenshot(str(OUT / "detail.svg"))
        print("wrote shots/detail.svg")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        app.save_screenshot(str(OUT / "checkpoint.svg"))
        print("wrote shots/checkpoint.svg")


def report_shot() -> None:
    """`mc week` as an SVG.

    The reports are plain ANSI on stdout rather than a Textual screen, so
    there is no save_screenshot to call. rich parses the escape codes back
    into styled text and writes a terminal frame matching the other shots.
    """
    import io

    from rich.console import Console
    from rich.text import Text

    from mission_control import config, report
    from mission_control.sessions import Store

    # Which projects appear depends on the day this is run: the week starts on
    # Monday, so activity dated five days back falls outside it by midweek.
    # The demo data keeps something recent in three projects for that reason.
    cfg, store = config.load(), Store()
    start, end = report.week_bounds(back=0)
    rep = report.build(cfg, store, start, end, "THIS WEEK")
    console = Console(record=True, width=84, file=io.StringIO(),
                      force_terminal=True, color_system="truecolor")
    console.print(Text.from_ansi(report.render(rep, colour=True)))
    console.save_svg(str(OUT / "week.svg"), title="mc week")
    print("wrote shots/week.svg")


def main() -> None:
    import asyncio
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build(tmp)
        asyncio.run(shoot(tmp))
        report_shot()


if __name__ == "__main__":
    main()
