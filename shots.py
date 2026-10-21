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
    "pgexercises-notes": [(5, 11), (4, 4)],
    "rally-cutter":      [(6, 3)],
    "harmonica-log":     [],
}

TITLES = {
    "orbital-sim": "Baseline PPO run and eval harness",
    "ascii-renderer": "Edge detection pass over the luminance ramp",
    "pgexercises-notes": "Window function exercises 1-12",
    "rally-cutter": "Audio onset detection spike",
}


def _entry(kind: str, when: datetime, **kw) -> str:
    return json.dumps({"type": kind,
                       "timestamp": when.astimezone(timezone.utc)
                                        .isoformat().replace("+00:00", "Z"),
                       **kw})


def _transcript(path: Path, project: Path, title: str, edits: int,
                when: datetime, live: bool) -> None:
    """One session's worth of plausible jsonl."""
    lines = [json.dumps({"type": "ai-title", "aiTitle": title}),
             _entry("user", when, message={"content": "carry on from yesterday"})]
    for i in range(edits):
        lines.append(_entry(
            "assistant", when + timedelta(seconds=i * 7),
            message={"content": [{"type": "tool_use", "name": "Edit",
                                  "input": {"file_path": str(project / f"src/mod{i % 5}.py")}}]}))
    # A session reads as live only while its file is fresh and its last
    # assistant entry has no end_turn, so the roster shows "working".
    if live:
        lines.append(_entry("assistant", datetime.now(),
                            message={"content": [{"type": "tool_use",
                                                  "name": "Bash", "input": {}}]}))
    path.write_text("\n".join(lines) + "\n")


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

    # A repo with history, so the selected row can show a real last commit.
    sim = root / "orbital-sim"
    env = {**os.environ, "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "d@e",
           "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "d@e"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sim, check=True)
    subprocess.run(["git", "add", "-A"], cwd=sim, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m",
                    "baselines: log episode return per seed, not just the mean"],
                   cwd=sim, check=True, env=env)

    now = datetime.now()
    for name, days in ACTIVITY.items():
        d = root / name
        slug = store / sessions.slug(str(d))
        slug.mkdir(parents=True, exist_ok=True)
        for i, (ago, edits) in enumerate(days):
            when = now - timedelta(days=ago, hours=2)
            f = slug / f"{name}-{i}.jsonl"
            _transcript(f, d, TITLES.get(name, name), edits, when,
                        live=(name == "ascii-renderer" and ago == 0))
            if ago:                      # backdate so week() bucketises it
                stamp = when.timestamp()
                os.utime(f, (stamp, stamp))

    cfg = tmp / "progress.toml"
    cfg.write_text("\n".join(lines))
    os.environ["MC_CONFIG"] = str(cfg)
    config.PATH = cfg
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


def main() -> None:
    import asyncio
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build(tmp)
        asyncio.run(shoot(tmp))


if __name__ == "__main__":
    main()
