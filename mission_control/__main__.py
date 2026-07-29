"""mc — mission control.

    mc                          the TUI
    mc init                     write a starter config
    mc recap [days]             sessions, day by day
    mc week [n]                 what happened this week (or n weeks ago)
    mc month [n]                same, for a calendar month
    mc doctor                   session-store health; exit 1 if anything is stranded
    mc fix [--go]               relink stranded slugs to where projects now live
    mc mv <project> <dest>      move a project, carrying its sessions with it
    mc retire <project> [--go]  drop a finished project out of --resume
    mc brief [path]             phase + next check for the project at path (default: cwd)
    mc new <name>               scaffold a project dir, add it to progress.toml, launch claude
    mc resume <project>         start/resume the project in the tmux pane to the left

Anything that mutates is a dry run unless you pass --go.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import activity, checks, config
from .sessions import Store


def cmd_recap(days: int = 1) -> int:
    cfg, store = config.load(), Store()
    today = datetime.now().date()
    rows: dict[str, list] = {}
    for p in cfg.projects:
        for f in store.files_for(str(p.path), p.aliases):
            # mtime is only a prefilter, and a permissive one: moving, copying or
            # backing up a transcript touches it without any work happening. The
            # timestamps inside are what actually happened.
            if (today - datetime.fromtimestamp(f.stat().st_mtime).date()).days > days:
                continue
            s = store.session(f)
            if s.ended and (today - s.ended.date()).days < days:
                rows.setdefault(p.name, []).append(s)
    if not rows:
        print("nothing today")
        return 0
    for name, sessions in sorted(rows.items()):
        print(f"\n\033[36m▌ {name}\033[0m")
        for s in sorted(sessions, key=lambda s: s.ended):
            edits = sum(s.edits.values())
            print(f"  {s.started:%H:%M}–{s.ended:%H:%M}  {s.title or '(untitled)'}")
            print(f"          \033[2m{s.prompts} prompts · {edits} edits\033[0m")
    store.flush()
    return 0


def cmd_report(period: str, back: int) -> int:
    """What happened across every project in a week or a month."""
    from . import report
    cfg, store = config.load(), Store()
    if period == "month":
        start, end = report.month_bounds(back=back)
        label = f"{start:%B %Y}"
    else:
        start, end = report.week_bounds(back=back)
        label = "THIS WEEK" if back == 0 else f"{back} WEEK(S) AGO"
    rep = report.build(cfg, store, start, end, label)
    print(report.render(rep, colour=sys.stdout.isatty()))
    store.flush()
    return 0


def cmd_doctor() -> int:
    cfg, store = config.load(), Store()
    if not config.PATH.exists():
        print(f"no config at {config.PATH}\n\n"
              f"  mc init          write a starter config there\n"
              f"  MC_CONFIG=...    point at one somewhere else")
        return 2
    if not cfg.projects:
        # Fresh install: everything on disk is "unknown" by definition. That is
        # discovery, not damage, and shouldn't be reported with warning signs.
        found = store.classify([], cfg.ignore)
        rows = [(d, cwd) for k in ("stranded", "untracked", "nested")
                for d, cwd in found[k] if cwd]
        print(f"No projects configured yet in {config.PATH}\n")
        if rows:
            print(f"Found {len(rows)} director{'y' if len(rows) == 1 else 'ies'} "
                  f"with Claude Code sessions:\n")
            for d, cwd in sorted(rows, key=lambda r: str(r[1]))[:20]:
                n = len(list(d.glob("*.jsonl")))
                print(f"    {n:>2} session(s)  {cwd}")
            print("\nAdd the ones you want to track as [projects.\"name\"] blocks.")
        return 0

    problems = 0
    print("PROJECTS")
    for p in sorted(cfg.projects, key=lambda p: p.name.lower()):
        if not p.visible:
            continue
        if not p.exists:
            print(f"  ! {p.name:<26} path missing: {p.path}")
            problems += 1
            continue
        n = len(store.files_for(str(p.path), p.aliases))
        ok, total, pct = checks.progress(p)
        prog = f"{ok}/{total} checks" if total else "no checks"
        print(f"    {p.name:<26} {n:>2} sessions   {prog}")
    buckets = store.classify(cfg.paths, cfg.ignore)
    for kind in ("stranded", "untracked", "nested", "ignored"):
        rows = buckets[kind]
        if not rows:
            continue
        print(f"\n{kind.upper()} ({len(rows)})")
        for d, cwd in rows:
            n = len(list(d.glob("*.jsonl")))
            mark = "!" if kind == "stranded" else " "
            print(f"  {mark} {d.name[:52]:<52} {n:>2}  {cwd or '—'}")
        if kind == "stranded":
            problems += len(rows)
    store.flush()
    print(f"\n{problems} problem(s)")
    return 1 if problems else 0


def _project(cfg, name: str):
    for p in cfg.projects:
        if p.name == name:
            return p
    print(f"unknown project: {name}", file=sys.stderr)
    return None


def cmd_mv(name: str, dest: str, go: bool) -> int:
    """Move a project and carry its whole session subtree with it."""
    from . import reconcile
    cfg = config.load()
    p = _project(cfg, name)
    if not p:
        return 2
    if not p.path.is_dir():
        print(f"{name}: path missing ({p.path}) — use `fix` instead", file=sys.stderr)
        return 2
    target = Path(dest).expanduser()
    if target.is_dir():
        target = target / p.path.name       # `mv proj ~/x/` means into x/
    if any(reconcile.is_live(d) for d in reconcile.slugs_under(p.path)):
        print(f"{name}: a session is still active. Claude caches its cwd at start,\n"
              f"so it would keep writing to the old slug after the move.\n"
              f"Quit it first, or run `mc fix` afterwards to sweep up.",
              file=sys.stderr)
        if not go:
            print()
        else:
            return 2
    ops = reconcile.plan_move(p.path, target)
    print(f"{'PLAN' if not go else 'APPLYING'}  ({len(ops)} operations)")
    for o in ops:
        print("  " + str(o).replace("\n", "\n  "))
    if not go:
        print("\ndry run — pass --go to apply, then update `path` in progress.toml")
        return 0
    ok, log = reconcile.apply(ops)
    print()
    for line in log:
        print("  " + line)
    print("\nnow update `path` for this project in progress.toml")
    return 0 if ok else 1


def cmd_fix(go: bool) -> int:
    """Relink stranded slugs to wherever their project actually lives now."""
    from . import reconcile
    cfg, store = config.load(), Store()
    stranded = store.classify(cfg.paths, cfg.ignore)["stranded"]
    if not stranded:
        print("nothing stranded")
        return 0
    # Only ever relink *into* a path the config already knows about. Without
    # this, a partial or wrong progress.toml lets a basename guess relocate real
    # session history to somewhere never asked for.
    known = {str(p.path).rstrip("/").lower() for p in cfg.projects}
    planned, unknown, live = [], [], []
    for d, cwd in stranded:
        guess = reconcile.suggest(cwd, cfg.roots)
        if guess and str(guess).rstrip("/").lower() not in known:
            guess = None
        if reconcile.is_live(d):
            live.append((d, cwd, guess))
        else:
            (planned if guess else unknown).append((d, cwd, guess))
    for d, cwd, guess in planned:
        print(f"  {d.name[:56]}\n    was {cwd}\n    now {guess}")
    for d, cwd, _ in unknown:
        print(f"  ! {d.name[:56]}\n    was {cwd or '—'} — no known project matches; "
              f"add it to progress.toml first")
    for d, cwd, _ in live:
        print(f"  ~ {d.name[:56]}\n    written to in the last 5 min — a live session "
              f"still has\n    the old cwd cached. Quit it, then re-run.")
    if not planned:
        return 1
    if not go:
        print(f"\ndry run — pass --go to relink {len(planned)}")
        return 0
    rc = 0
    for d, _, guess in planned:
        ok, log = reconcile.apply(reconcile.plan_repair(d, guess))
        for line in log:
            print("  " + line)
        rc |= 0 if ok else 1
    return rc


def cmd_retire(name: str, go: bool) -> int:
    """Drop a finished project out of --resume. Moves, never deletes."""
    from . import reconcile
    cfg, store = config.load(), Store()
    p = _project(cfg, name)
    if not p:
        return 2
    dirs = reconcile.slugs_under(p.path)
    if any(reconcile.is_live(d) for d in dirs):
        print(f"{name}: a session is still active here — quit it first", file=sys.stderr)
        return 2
    if not dirs:
        print(f"{name}: no session dirs found")
        return 0
    ok, log = reconcile.retire(name, dirs, dry_run=not go)
    for line in log:
        print("  " + line.replace("\n", "\n  "))
    if not go:
        print("\ndry run — pass --go to apply")
    return 0 if ok else 1


def _tildify(p: Path) -> str:
    """Write ~ back into a path so generated config stays portable."""
    home = str(Path.home())
    sp = str(p)
    return "~" + sp[len(home):] if sp.startswith(home) else sp


def latest_session_id(store, p) -> str | None:
    """The session `--resume` should pick up.

    Deliberately read from our own session store rather than `lastSessionId` in
    ~/.claude.json: that file still holds stale entries for projects that have
    since moved, whereas the slug dirs are kept correct by `fix`/`mv`.
    """
    recent = store.recent(str(p.path), p.aliases, limit=1)
    return recent[0].session_id if recent else None


def cmd_resume(name: str, new_win: bool, print_only: bool) -> int:
    from . import launcher
    cfg, store = config.load(), Store()
    p = _project(cfg, name)
    if not p:
        return 2
    if not p.path.is_dir():
        print(f"{name}: path missing ({p.path})", file=sys.stderr)
        return 2

    sid = latest_session_id(store, p)
    cmd = launcher.resume_command(p.path, sid)
    store.flush()

    if print_only:
        print(cmd)
        return 0

    if new_win:
        ok, msg = launcher.new_window(p.path, launcher.resume_argv(sid), name=name)
        print(f"{'✓' if ok else '✗'} {msg}")
        return 0 if ok else 1

    target = launcher.resolve_target()
    if target.usable:
        ok, msg = launcher.send(target, cmd)
        print(f"{'✓' if ok else '✗'} {msg}: {cmd}")
        return 0 if ok else 1

    print(f"✗ {target.problem}")
    print(f"  {cmd}")
    if launcher.copy(cmd):
        print("  (copied to clipboard)")
    print("  or: mc resume {} --new-window".format(name))
    return 1


def find_project_for_path(cfg, target: Path):
    """Longest-prefix match: a session opened inside a project's subdirectory
    still belongs to that project, and if projects were ever nested the more
    specific one should win."""
    target = target.resolve()
    best = None
    for p in cfg.projects:
        if not p.visible or not p.exists:
            continue
        root = p.path.resolve()
        if target == root or str(target).startswith(str(root) + os.sep):
            if best is None or len(str(root)) > len(str(best.path.resolve())):
                best = p
    return best


def _brief_lines(target: str | None) -> list[str]:
    cfg = config.load()
    p = find_project_for_path(cfg, Path(target or os.getcwd()))
    if not p:
        return []
    ok, total, pct = checks.progress(p)
    headline = f"{p.phase} ({p.status})" if p.phase else p.status
    lines = [f"mission-control: {p.name} — {headline}"]
    if total:
        lines.append(f"{ok}/{total} checks passing ({round(pct)}%)")
        # Only ever suggest a *fast* check here — this runs on every session
        # start, so a `cmd`/`gh_pr` check that shells out or hits the network
        # is never a candidate, evaluated or not.
        nxt = next(
            (c for c in p.checks if checks.is_fast(c) and checks.evaluate_fast(p, c) is False),
            None,
        )
        if nxt:
            detail = nxt.value or ("mark done manually" if nxt.type == "manual" else "")
            lines.append(f"next: {nxt.name}" + (f" — {detail}" if detail else ""))
    else:
        lines.append('no checks defined yet — run /init-project')
    lc = activity.last_commit(p.path)
    if lc:
        lines.append(f"last commit ({lc.when}): {lc.subject}")
    return lines


def cmd_brief(target: str | None, as_hook: bool) -> int:
    lines = _brief_lines(target)
    if as_hook:
        # SessionStart hooks are not free-text: stdout must be this exact
        # envelope or Claude Code silently drops it instead of adding context.
        if lines:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "\n".join(lines),
                }
            }))
        else:
            print("{}")
        return 0
    print("\n".join(lines) if lines else "no tracked project matches this directory")
    return 0


STARTER = '# mission-control — intent only. Activity is derived, never recorded here.\n# https://github.com/StanKarz/mission-control\n#\n# status: active | blocked | done | archived | ignored\n\n[meta]\n# Where projects live. `mc new` uses the first; `mc fix` searches all of them\n# when working out where a moved project went.\nproject_roots = [\n  "~/projects",\n]\n\n# Directories that have Claude Code sessions but are not projects.\n# Exact matches only.\nignore = [\n]\n\n# Asked at month end, by `c` in the TUI. Yours to write.\ncheckpoint_questions = [\n]\n\n# ── projects ────────────────────────────────────────────────────────────\n# Add one block per project, then run /init-project inside it to define\n# what "done" means as checks the app can actually evaluate.\n#\n# [projects."my-project"]\n# path   = "~/projects/my-project"\n# status = "active"\n# phase  = "prototype"\n#\n#   [[projects."my-project".checks]]\n#   name  = "tests pass"\n#   type  = "cmd"          # path | cmd | git_tag | gh_pr | manual\n#   value = "pytest -q"\n'


def cmd_init(force: bool) -> int:
    """Write a starter config, so a fresh install has somewhere to begin."""
    path = config.PATH
    if path.exists() and not force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER)
    print(f"wrote {path}\n\nAdd a project block, then run /init-project inside it.")
    return 0


def cmd_new(name: str, launch: bool) -> int:
    """Scaffold a project: mkdir, git init, an entry in progress.toml."""
    import re as _re
    root = cfg.new_project_root / name
    if root.exists():
        print(f"{root} already exists", file=sys.stderr)
        return 2
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root)

    cfg_path = config.PATH
    text = cfg_path.read_text() if cfg_path.exists() else ""
    block = (
        f'\n[projects."{name}"]\n'
        f'path   = "{_tildify(root)}"\n'
        f'status = "active"\n'
        f'# checks = run /init-project to define what "done" means here\n'
    )
    m = _re.search(r"^#.*archived.*$", text, _re.MULTILINE)
    text = text[: m.start()] + block + "\n" + text[m.start():] if m else text + block
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(text)

    print(f"created {root}")
    print(f'added [projects."{name}"] to {cfg_path}')
    print("run /init-project once you're in to define what done looks like")
    if launch:
        os.chdir(root)
        os.execvp("claude", ["claude"])
    return 0


def main() -> int:
    argv = sys.argv[1:]
    go = "--go" in argv
    as_hook = "--hook" in argv
    launch = "--no-launch" not in argv
    new_win = "--new-window" in argv
    print_only = "--print" in argv
    argv = [a for a in argv
            if a not in ("--go", "--hook", "--no-launch", "--new-window", "--print")]
    cmd = argv[0] if argv else ""
    if cmd == "recap":
        return cmd_recap(int(argv[1]) if len(argv) > 1 else 1)
    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "fix":
        return cmd_fix(go)
    if cmd == "mv":
        if len(argv) < 3:
            print("usage: mc mv <project> <destination> [--go]", file=sys.stderr)
            return 2
        return cmd_mv(argv[1], argv[2], go)
    if cmd == "retire":
        if len(argv) < 2:
            print("usage: mc retire <project> [--go]", file=sys.stderr)
            return 2
        return cmd_retire(argv[1], go)
    if cmd == "resume":
        if len(argv) < 2:
            print("usage: mc resume <project> [--new-window] [--print]", file=sys.stderr)
            return 2
        return cmd_resume(argv[1], new_win, print_only)
    if cmd in ("week", "month", "report"):
        back = int(argv[1]) if len(argv) > 1 and argv[1].lstrip("-").isdigit() else 0
        return cmd_report("month" if cmd == "month" else "week", abs(back))
    if cmd == "init":
        return cmd_init(go)
    if cmd == "brief":
        return cmd_brief(argv[1] if len(argv) > 1 else None, as_hook)
    if cmd == "new":
        if len(argv) < 2:
            print("usage: mc new <name> [--no-launch]", file=sys.stderr)
            return 2
        return cmd_new(argv[1], launch)
    if cmd:
        print(__doc__)
        return 2
    from .app import MissionControl
    MissionControl().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
