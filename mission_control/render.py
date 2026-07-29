"""Palette and small renderables. Colour encodes status, never magnitude."""

from __future__ import annotations

BG      = "#16161e"
PANEL   = "#1a1b26"
FG      = "#c0caf5"
DIM     = "#565f89"
FAINT   = "#292e42"
CYAN    = "#7dcfff"
BLUE    = "#7aa2f7"
GREEN   = "#9ece6a"
AMBER   = "#e0af68"
RED     = "#f7768e"
MAGENTA = "#bb9af7"
TEAL    = "#73daca"

# status -> (colour, glyph)
STATUS = {
    "active":   (TEAL,    "●"),
    "blocked":  (MAGENTA, "◐"),
    "stranded": (AMBER,   "▲"),
    "done":     (BLUE,    "✦"),
    "archived": (DIM,     "○"),
    "ignored":  (FAINT,   "·"),
}


def lerp(a: str, b: str, t: float) -> str:
    ar, ag, ab = (int(a[i : i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(b[i : i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


def bar(pct: float, width: int = 16, lo: str = TEAL, hi: str = BLUE) -> str:
    filled = round(width * pct / 100)
    return "".join(
        f"[{lerp(lo, hi, i / max(width - 1, 1))}]━[/]" if i < filled else f"[{FAINT}]━[/]"
        for i in range(width)
    )


def spark(vals: list[int], colour: str = CYAN, hi: int | None = None) -> str:
    """`hi` scales against a shared maximum so one project's quiet week doesn't
    render identically to another's busy one."""
    if not any(vals):
        return f"[{FAINT}]{'╌' * len(vals)}[/]"
    blocks = "▁▂▃▄▅▆▇█"
    hi = max(hi or 0, max(vals))
    out = []
    for v in vals:
        if v == 0:
            out.append(f"[{FAINT}]▁[/]")
        else:
            ch = blocks[min(int(v / hi * (len(blocks) - 1)), len(blocks) - 1)]
            out.append(f"[{lerp(DIM, colour, v / hi)}]{ch}[/]")
    return "".join(out)


def heat(vals: list[int]) -> str:
    hi = max(vals) or 1
    return " ".join(
        f"[{FAINT}]■[/]" if v == 0 else f"[{lerp('#2c4a3e', GREEN, v / hi)}]■[/]"
        for v in vals
    )


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    m = seconds / 60
    if m < 60:
        return f"{int(m)}m ago"
    if m < 60 * 48:
        return f"{int(m / 60)}h ago"
    return f"{int(m / 1440)}d ago"


def fit(s: str, n: int) -> str:
    """Truncate to n columns with an ellipsis, then pad. Plain text only —
    always call this *before* wrapping in markup, never after."""
    return (s if len(s) <= n else s[: n - 1] + "…").ljust(n)
