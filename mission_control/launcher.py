"""Driving the other tmux pane.

The roster is on the right; work happens on the left. This turns the roster
from something you look at into the thing you start the day from.

Three tmux behaviours found by testing rather than assumption:

1.  Relative targets like ``{left-of}`` resolve against the *invoking* process's
    ``$TMUX_PANE``, so running this from inside the roster pane picks the pane
    to its left, which is what we want.
2.  ``display-message -p -t '{left-of}'`` in a single-pane window prints nothing
    and **exits 0**. Exit status is therefore useless as a guard; emptiness of
    the output is the real signal.
3.  ``send-keys`` to an unresolvable target fails cleanly ("can't find pane",
    exit 1) rather than typing into the invoking pane — so the dangerous
    "roster types into itself" case cannot happen.

The guard that actually earns its keep is (4): if the target pane is running
something other than a shell, keystrokes are *input to that program*. In the
intended layout the left pane is usually running Claude, so sending a resume
command there would submit it as a prompt rather than run it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Foreground commands that mean "a prompt is waiting", so typing is safe.
SHELLS = {"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "elvish"}

DEFAULT_TARGET = "{left-of}"


def in_tmux() -> bool:
    return bool(os.environ.get("TMUX"))


def _tmux(*args: str, timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


@dataclass
class Target:
    pane: str | None = None
    command: str | None = None      # foreground command in that pane
    problem: str | None = None      # human-readable reason it can't be used

    @property
    def usable(self) -> bool:
        return self.pane is not None and self.problem is None


def resolve_target(spec: str | None = None) -> Target:
    """Find the pane to send work to, and decide whether it's safe to."""
    if not in_tmux():
        return Target(problem="not running inside tmux")

    spec = spec or os.environ.get("MC_TARGET_PANE") or DEFAULT_TARGET

    rc, out = _tmux("display-message", "-p", "-t", spec, "#{pane_id}")
    # Emptiness, not exit status — see module docstring (2).
    if rc != 0 or not out or not out.startswith("%"):
        return Target(problem=f"no pane matches {spec!r} (single pane window?)")

    pane = out
    if pane == os.environ.get("TMUX_PANE"):
        return Target(pane=pane, problem="target resolves to this pane")

    rc, cmd = _tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    cmd = cmd if rc == 0 else ""
    if cmd and cmd not in SHELLS:
        return Target(pane=pane, command=cmd,
                      problem=f"{cmd} is running there — keys would go to it, not a shell")
    return Target(pane=pane, command=cmd or None)


def resume_argv(session_id: str | None) -> str:
    """Just the claude invocation, for when the cwd is already handled."""
    return f"claude --resume {session_id}" if session_id else "claude"


def resume_command(path: Path, session_id: str | None) -> str:
    """The full line a shell should run to pick this project back up."""
    return f"cd {shlex.quote(str(path))} && {resume_argv(session_id)}"


def send(target: Target, command: str) -> tuple[bool, str]:
    if not target.usable:
        return False, target.problem or "no usable target"
    rc, out = _tmux("send-keys", "-t", target.pane, command, "Enter")
    if rc != 0:
        return False, out or "send-keys failed"
    return True, f"sent to {target.pane}"


def new_window(path: Path, command: str, name: str | None = None) -> tuple[bool, str]:
    """Escape hatch for when the target pane is busy: a fresh window always works."""
    if not in_tmux():
        return False, "not running inside tmux"
    args = ["new-window", "-c", str(path)]
    if name:
        args += ["-n", name]
    args.append(command)
    rc, out = _tmux(*args)
    return (rc == 0), (f"opened new window" if rc == 0 else out or "new-window failed")


def copy(text: str) -> bool:
    for tool in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            p = subprocess.run(tool, input=text, text=True, timeout=5)
            if p.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False
