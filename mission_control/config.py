"""progress.toml — the source of truth for intent.

Loaded with tomlkit so hand-written comments and key order survive a round trip;
editing this file in Vim is a first-class workflow, not a fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import tomlkit

def _default_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "mission-control" / "progress.toml"


PATH = Path(os.environ["MC_CONFIG"]).expanduser() if os.environ.get("MC_CONFIG") \
    else _default_config_path()

# "paused" and "archived" are deliberately different: paused work is coming
# back and stays on the roster, archived work is filed away and does not.
VALID_STATUS = ("active", "blocked", "paused", "done", "archived", "ignored")

# Used when [meta] says nothing. Deliberately conservative: one root, and no
# assumptions about anyone's home directory layout beyond it.
DEFAULT_ROOTS = ["~/projects"]


@dataclass
class Check:
    name: str
    type: str
    value: str = ""
    done: bool = False


@dataclass
class Project:
    name: str
    path: Path
    status: str = "active"
    phase: str = ""
    desc: str = ""
    repos: list[Path] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    @property
    def visible(self) -> bool:
        return self.status != "ignored"


@dataclass
class Config:
    doc: tomlkit.TOMLDocument
    projects: list[Project]
    questions: list[str]
    roots: list[Path] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        return [str(p.path) for p in self.projects]

    @property
    def new_project_root(self) -> Path:
        """Where `mc new` puts things: the first configured root."""
        return self.roots[0] if self.roots else _expand(DEFAULT_ROOTS[0])

    # -- monthly checkpoint ------------------------------------------------
    def answers_for(self, period: str) -> list[str]:
        """Answers recorded for a "YYYY-MM" period, or empty."""
        block = (self.doc.get("checkpoints") or {}).get(period) or {}
        return [str(a) for a in block.get("answers", [])]

    def set_status(self, name: str, status: str) -> None:
        """Change a project's status in place, preserving everything else."""
        block = (self.doc.get("projects") or {}).get(name)
        if block is not None and status in VALID_STATUS:
            block["status"] = status

    def set_answers(self, period: str, answers: list[str]) -> None:
        """Write answers into the document, creating the table as needed.

        Mutates `self.doc` only — call `save()` to persist. Kept separate so a
        failed write can never leave the in-memory config disagreeing with disk.
        """
        checkpoints = self.doc.get("checkpoints")
        if checkpoints is None:
            checkpoints = tomlkit.table(is_super_table=True)
            self.doc["checkpoints"] = checkpoints
        block = checkpoints.get(period)
        if block is None:
            block = tomlkit.table()
            checkpoints[period] = block
        arr = tomlkit.array()
        arr.multiline(True)
        for a in answers:
            arr.append(a)
        block["answers"] = arr
        block["answered"] = date.today().isoformat()


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def load(path: Path | None = None) -> Config:
    f = path or PATH
    if not f.exists():
        return Config(tomlkit.document(), [], [])
    doc = tomlkit.parse(f.read_text())
    projects = []
    for name, body in (doc.get("projects") or {}).items():
        status = str(body.get("status", "active"))
        if status not in VALID_STATUS:
            status = "active"
        projects.append(
            Project(
                name=name,
                path=_expand(str(body.get("path", ""))),
                status=status,
                phase=str(body.get("phase", "")),
                desc=str(body.get("desc", "")),
                repos=[_expand(str(r)) for r in body.get("repos", [])] or
                      [_expand(str(body.get("path", "")))],
                aliases=[str(a) for a in body.get("aliases", [])],
                checks=[
                    Check(
                        name=str(c.get("name", "?")),
                        type=str(c.get("type", "manual")),
                        value=str(c.get("value", "")),
                        done=bool(c.get("done", False)),
                    )
                    for c in body.get("checks", [])
                ],
            )
        )
    meta = doc.get("meta") or {}
    questions = [str(q) for q in meta.get("checkpoint_questions", [])]
    roots = [_expand(str(r)) for r in meta.get("project_roots", DEFAULT_ROOTS)]
    ignore = [str(i) for i in meta.get("ignore", [])]
    return Config(doc, projects, questions, roots, ignore)


def save(cfg: Config, path: Path | None = None) -> None:
    (path or PATH).write_text(tomlkit.dumps(cfg.doc))
