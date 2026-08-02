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
    reason: str = ""                # "no-tmux" | "no-pane" | "self" | "busy"

    @property
    def usable(self) -> bool:
        return self.pane is not None and self.problem is None

    @property
    def can_split(self) -> bool:
        """No neighbour to send to, but we are in tmux — so make one."""
        return self.reason == "no-pane"


def _neighbour_left() -> str | None:
    """The pane immediately left of *this* one, computed from geometry.

    tmux resolves relative targets like ``{left-of}`` against the session's
    **active** pane, not the pane whose process is asking. Those are usually
    the same — you press a key in the pane you are looking at — but not always:
    after this app opens a work pane, focus moves there, and a later query from
    the roster would then be answered relative to the wrong pane (and can wrap
    around to the far edge, pointing back at the roster itself).

    Reading the layout and picking the nearest pane whose right edge touches
    ours removes that ambiguity entirely.
    """
    me = os.environ.get("TMUX_PANE")
    if not me:
        return None
    rc, out = _tmux("list-panes", "-t", me,
                    "-F", "#{pane_id} #{pane_left} #{pane_right} #{pane_top} #{pane_bottom}")
    if rc != 0:
        return None
    geo: dict[str, tuple[int, int, int, int]] = {}
    for line in out.splitlines():
        bits = line.split()
        if len(bits) == 5:
            try:
                geo[bits[0]] = tuple(int(b) for b in bits[1:])  # type: ignore[assignment]
            except ValueError:
                continue
    if me not in geo:
        return None
    my_left, _, my_top, my_bottom = geo[me]
    best = None
    for pid, (_, right, top, bottom) in geo.items():
        if pid == me or right >= my_left:
            continue
        if bottom < my_top or top > my_bottom:      # no vertical overlap
            continue
        if best is None or right > geo[best][1]:    # nearest wins
            best = pid
    return best


def resolve_target(spec: str | None = None) -> Target:
    """Find the pane to send work to, and decide whether it's safe to."""
    if not in_tmux():
        return Target(problem="not running inside tmux", reason="no-tmux")

    spec = spec or os.environ.get("MC_TARGET_PANE") or DEFAULT_TARGET

    if spec == DEFAULT_TARGET:
        pane = _neighbour_left()
        if not pane:
            return Target(problem="no pane to the left", reason="no-pane")
    else:
        rc, out = _tmux("display-message", "-p", "-t", spec, "#{pane_id}")
        # Emptiness, not exit status — see module docstring (2).
        if rc != 0 or not out or not out.startswith("%"):
            return Target(problem=f"no pane matches {spec!r}", reason="no-pane")
        pane = out
    if pane == os.environ.get("TMUX_PANE"):
        return Target(pane=pane, problem="target resolves to this pane", reason="self")

    rc, cmd = _tmux("display-message", "-p", "-t", pane, "#{pane_current_command}")
    cmd = cmd if rc == 0 else ""
    if cmd and cmd not in SHELLS:
        return Target(pane=pane, command=cmd, reason="busy",
                      problem=f"{cmd} is running there — keys would go to it, not a shell")
    return Target(pane=pane, command=cmd or None)


def split_for_work(path: Path, command: str, width: str = "60%") -> tuple[bool, str]:
    """Open a work pane to the *left* of the roster and run `command` in it.

    For when the roster is alone in its window: rather than refusing, build the
    layout the app assumes — work on the left, roster on the right. ``-b`` puts
    the new pane before the current one, so the roster keeps the right-hand
    side and does not have to move.

    Focus follows the new pane (no ``-d``), because pressing this key means you
    intend to start working there. When the command exits, tmux closes the pane
    and the roster gets the full width back.
    """
    if not in_tmux():
        return False, "not running inside tmux"
    rc, out = _tmux("split-window", "-h", "-b", "-l", width,
                    "-c", str(path), command)
    return (rc == 0), ("opened a pane to the left" if rc == 0
                       else out or "split-window failed")


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
