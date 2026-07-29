"""Moving projects without orphaning their sessions.

The only module allowed to mutate anything outside ``progress.toml``. Every
operation archives first and verifies transcript line counts afterwards.

Four traps, each hit for real before being encoded here:

1.  A project's sessions are not one directory. A sub-repo nested inside a
    project gets its own slug, so a move must carry every slug *beneath* the
    project, not just its root.
2.  macOS is case-insensitive. Renaming ``…-Projects-x`` to ``…-projects-x`` is
    a rename onto the same directory: it must hop via a temp name, or the
    "merge" branch moves files onto themselves and then fails to clean up.
3.  Slugs cannot be decoded — ``/``, ``_`` and ``.`` all encode to ``-``. Always
    encode forwards from a known path.
4.  ``mtime`` is not evidence of work; moving a file updates it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .sessions import STORE, slug

ARCHIVE = Path.home() / ".claude" / "archive"


@dataclass
class Op:
    kind: str  # "dir" | "slug"
    src: Path
    dst: Path

    def __str__(self) -> str:
        h = str(Path.home())
        return (f"{self.kind:4} {str(self.src).replace(h, '~')}\n"
                f"     -> {str(self.dst).replace(h, '~')}")


def _lines(d: Path) -> dict[str, int]:
    return {f.name: sum(1 for _ in f.open(errors="replace")) for f in d.glob("*.jsonl")}


def claude_cwds() -> set[Path]:
    """Working directories of running Claude processes.

    Grepping the command line does not work — cwd never appears there. This
    reads it from the kernel via lsof.
    """
    import subprocess
    out: set[Path] = set()
    try:
        pids = subprocess.run(["pgrep", "-f", "claude"], capture_output=True,
                              text=True, timeout=5).stdout.split()
        for pid in pids:
            r = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if line.startswith("n"):
                    out.add(Path(line[1:]))
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def is_live(slug_dir: Path, within: int = 300) -> bool:
    """Whether a session is probably still writing here.

    Claude Code captures its cwd *string* at session start and reuses it for the
    lifetime of the process. Move the directory and the running session keeps
    writing to the old slug — so repairing it mid-flight just gets undone, and
    the transcript ends up split across two locations. Recent writes are the
    reliable signal; the process's own cwd is not, because on macOS it follows
    the inode across a move and so points at the *new* path.
    """
    import time
    return any(time.time() - f.stat().st_mtime < within for f in slug_dir.glob("*.jsonl"))


def slugs_under(path: Path) -> list[Path]:
    """Every slug dir for this project *and* anything nested inside it."""
    root = slug(path).lower()
    if not STORE.exists():
        return []
    return sorted(
        d for d in STORE.iterdir()
        if d.is_dir() and (d.name.lower() == root or d.name.lower().startswith(root + "-"))
    )


def plan_move(src: Path, dst: Path) -> list[Op]:
    """Move a project and re-slug its whole session subtree.

    Assumes slugs are currently well-formed for ``src`` — run ``doctor`` first.
    The suffix carry is what handles nesting: a sub-repo's slug is
    ``slug(src) + <encoded subpath>``, so re-rooting is a string swap.
    """
    src, dst = Path(src).expanduser(), Path(dst).expanduser()
    ops = [Op("dir", src, dst)]
    root = slug(src)
    for d in slugs_under(src):
        suffix = d.name[len(root):]  # "" for the project root, "-sub-repo" below it
        ops.append(Op("slug", d, STORE / (slug(dst) + suffix)))
    return ops


def plan_repair(slug_dir: Path, new_path: Path) -> list[Op]:
    """Point one stranded slug dir at a path that exists. Does not move files on
    disk — the project is already where it should be; only the slug is wrong."""
    return [Op("slug", Path(slug_dir), STORE / slug(Path(new_path).expanduser()))]


def suggest(recorded_cwd: str | None, roots: list[Path]) -> Path | None:
    """Find where a stranded project probably went, by basename under `roots`."""
    if not recorded_cwd:
        return None
    stem = Path(recorded_cwd).name
    for r in roots:
        r = Path(r).expanduser()
        if not r.is_dir():
            continue
        for cand in r.iterdir():
            if cand.is_dir() and cand.name == stem:
                return cand
    return None


def _count(f: Path) -> int:
    try:
        with f.open(errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return -1


def _merge_dir(src: Path, dst: Path) -> None:
    """Move src's contents into dst, without ever losing transcript history.

    Two slug directories can hold the **same session id** — a live session
    keeps writing to its old slug after a move, so the old directory
    regenerates a short stub of a transcript that also exists, much longer, in
    the new one. Resolving that by newest mtime would overwrite the long file
    with the stub, because the stub is what was written most recently.

    Transcripts are append-only, so more lines means more history: for
    ``.jsonl`` the longer file wins regardless of mtime. Anything else falls
    back to newest-wins. Losers are left in place for the archive copy rather
    than deleted, and the source directory is only removed once empty.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        target = dst / item.name
        if not target.exists():
            shutil.move(str(item), str(target))
        elif item.is_dir():
            _merge_dir(item, target)
        elif item.suffix == ".jsonl":
            if _count(item) > _count(target):
                shutil.move(str(item), str(target))
        elif item.stat().st_mtime > target.stat().st_mtime:
            shutil.move(str(item), str(target))
    if src.is_dir() and not any(src.iterdir()):
        src.rmdir()


def _move_slug(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve() and src.name == dst.name:
        return
    if src.name.lower() == dst.name.lower() and src.name != dst.name:
        # Case-only rename on a case-insensitive filesystem: hop via a temp name,
        # otherwise this is a rename onto itself and the merge branch corrupts.
        tmp = src.parent / f".tmp-reslug-{src.name}"
        src.rename(tmp)
        tmp.rename(dst)
    elif dst.exists():
        _merge_dir(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def apply(ops: list[Op], *, dry_run: bool = False, tag: str = "") -> tuple[bool, list[str]]:
    """Archive, execute, verify. Returns (ok, log)."""
    log: list[str] = []
    if dry_run:
        return True, [str(o) for o in ops]

    stamp = ARCHIVE / f"reconcile-{tag or date.today().isoformat()}"
    # Expected line count per destination file. A merge means the destination
    # may already hold a file of the same name, so the expectation is the
    # *larger* of the two — comparing only against the source reported a
    # mismatch on a perfectly good merge, which trains you to ignore the check.
    before: dict[Path, dict[str, int]] = {}

    for o in ops:
        if o.kind == "slug" and o.src.is_dir():
            shutil.copytree(o.src, stamp / o.src.name, dirs_exist_ok=True)
            expect = dict(_lines(o.dst)) if o.dst.is_dir() else {}
            for name, n in _lines(o.src).items():
                expect[name] = max(expect.get(name, 0), n)
            before[o.dst] = expect
    if before:
        log.append(f"archived {len(before)} slug dir(s) to {stamp}")

    for o in ops:
        if o.kind == "dir":
            if not o.src.is_dir():
                return False, log + [f"missing source directory: {o.src}"]
            if o.dst.exists():
                return False, log + [f"destination already exists: {o.dst}"]
            o.dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(o.src), str(o.dst))
            log.append(f"moved dir  {o.src.name}")
        else:
            if not o.src.is_dir():
                continue
            _move_slug(o.src, o.dst)
            log.append(f"moved slug {o.dst.name[:56]}")

    ok = True
    for dst, counts in before.items():
        after = _lines(dst)
        if after != counts:
            ok = False
            log.append(f"MISMATCH {dst.name}: {counts} -> {after}")
        else:
            total = sum(counts.values())
            log.append(f"verified   {dst.name[:48]}  {total} lines")
    return ok, log


def retire(name: str, slug_dirs: list[Path], *, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Drop a project out of `claude --resume` without deleting anything.

    Moving the slug dir is what removes it from the picker; the transcripts stay
    on disk and moving the directory back fully restores it.
    """
    dest = ARCHIVE / "shipped" / name
    if dry_run:
        return True, [f"slug {d.name}\n     -> {dest / d.name}" for d in slug_dirs]
    log = []
    for d in slug_dirs:
        if not d.is_dir():
            continue
        target = dest / d.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _merge_dir(d, target)
        else:
            shutil.move(str(d), str(target))
        log.append(f"retired {d.name[:56]} -> {target}")
    return True, log or [f"{name}: no sessions to retire"]
