"""Tests for the pure logic.

Weighted towards the places where a bug is silent and expensive: slug encoding
(gets it wrong and history is orphaned), reconcile planning (gets it wrong and
history is *moved* somewhere wrong), and the date arithmetic behind reports.

Anything touching the real ~/.claude store or a live tmux server is left to
manual verification — the value there was in observing real behaviour, and a
mock of a wrong assumption just enshrines the wrong assumption.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from mission_control import checks, config, modals, report
from mission_control.reconcile import Op, plan_move, plan_repair
from mission_control.sessions import slug


# ── slug encoding ────────────────────────────────────────────────────────
@pytest.mark.parametrize("path,expected", [
    ("/Users/x/projects/blog", "-Users-x-projects-blog"),
    ("/Users/x/my_app", "-Users-x-my-app"),          # underscore -> dash
    ("/Users/x/.config/ghostty", "-Users-x--config-ghostty"),   # dot -> dash
    ("/Users/x/a.b_c-d", "-Users-x-a-b-c-d"),
])
def test_slug_encodes_forwards(path, expected):
    assert slug(path) == expected


def test_slug_is_lossy_and_therefore_never_decodable():
    """Three different paths collapse to one slug. This is *why* the code only
    ever encodes forwards and recovers real paths from the cwd field."""
    assert slug("/x/my-app") == slug("/x/my_app") == slug("/x/my.app")


# ── reconcile planning ───────────────────────────────────────────────────
def test_plan_move_carries_nested_sub_repos(tmp_path, monkeypatch):
    """A sub-repo inside a project has its own slug. Moving the parent must
    re-slug the whole subtree, or the nested session is silently orphaned."""
    store = tmp_path / "store"
    store.mkdir()
    src, dst = Path("/w/proj"), Path("/w/archive/proj")
    for name in (slug(src), slug(src) + "-vendor-lib"):
        (store / name).mkdir()
    monkeypatch.setattr("mission_control.reconcile.STORE", store)

    ops = plan_move(src, dst)
    slugs = {o.dst.name for o in ops if o.kind == "slug"}
    assert slug(dst) in slugs
    assert slug(dst) + "-vendor-lib" in slugs, "nested sub-repo was dropped"
    assert sum(o.kind == "dir" for o in ops) == 1


def test_plan_move_is_a_noop_shape_when_nothing_is_tracked(tmp_path, monkeypatch):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr("mission_control.reconcile.STORE", store)
    ops = plan_move(Path("/w/proj"), Path("/w/other"))
    assert [o.kind for o in ops] == ["dir"]


def test_plan_repair_only_renames_the_slug(tmp_path):
    ops = plan_repair(tmp_path / "-old-slug", Path("/w/projects/blog"))
    assert len(ops) == 1 and ops[0].kind == "slug"
    assert ops[0].dst.name == slug("/w/projects/blog")


# ── checks engine ────────────────────────────────────────────────────────
def _project(tmp_path, checks_):
    return config.Project(name="p", path=tmp_path, checks=checks_)


def test_path_check_reflects_the_filesystem(tmp_path):
    c = config.Check(name="artifact", type="path", value="out/result.json")
    p = _project(tmp_path, [c])
    assert checks.evaluate_fast(p, c) is False
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "result.json").touch()
    assert checks.evaluate_fast(p, c) is True


def test_manual_check_uses_its_flag(tmp_path):
    done = config.Check(name="a", type="manual", done=True)
    todo = config.Check(name="b", type="manual", done=False)
    p = _project(tmp_path, [done, todo])
    assert checks.evaluate_fast(p, done) is True
    assert checks.evaluate_fast(p, todo) is False


def test_progress_is_recomputed_not_remembered(tmp_path):
    c = config.Check(name="artifact", type="path", value="done.txt")
    p = _project(tmp_path, [c, config.Check(name="m", type="manual", done=True)])
    assert checks.progress(p) == (1, 2, 50.0)
    (tmp_path / "done.txt").touch()
    assert checks.progress(p) == (2, 2, 100.0), "must re-measure, not cache"


def test_no_checks_reports_zero_total_not_zero_percent(tmp_path):
    passing, total, pct = checks.progress(_project(tmp_path, []))
    assert (passing, total) == (0, 0)


def test_slow_checks_are_not_evaluated_inline(tmp_path):
    """cmd/gh_pr must never run during a render — they shell out or hit
    the network, and `brief` runs on every session start."""
    cmd = config.Check(name="tests", type="cmd", value="exit 0")
    assert checks.is_fast(cmd) is False
    assert checks.evaluate_fast(_project(tmp_path, [cmd]), cmd) is None


def test_unresolved_slow_checks_never_overstate_progress(tmp_path):
    p = _project(tmp_path, [config.Check(name="t", type="cmd", value="true")])
    assert checks.progress(p)[0] == 0


# ── checkpoint window ────────────────────────────────────────────────────
@pytest.mark.parametrize("day,expected", [
    (date(2026, 7, 28), False), (date(2026, 7, 29), True), (date(2026, 7, 31), True),
    (date(2026, 12, 29), True),                        # year rollover
    (date(2026, 2, 25), False), (date(2026, 2, 26), True),   # 28-day February
    (date(2024, 2, 26), False), (date(2024, 2, 27), True),   # leap February
    (date(2026, 8, 1), False),
])
def test_checkpoint_opens_only_at_month_end(day, expected):
    assert modals.is_open(day) is expected


# ── report periods ───────────────────────────────────────────────────────
def test_week_bounds_run_monday_to_sunday():
    start, end = report.week_bounds(date(2026, 7, 29))   # a Wednesday
    assert start == date(2026, 7, 27) and start.weekday() == 0
    assert end == date(2026, 8, 2) and end.weekday() == 6


def test_week_bounds_go_backwards():
    assert report.week_bounds(date(2026, 7, 29), back=1)[0] == date(2026, 7, 20)


@pytest.mark.parametrize("day,first,last", [
    (date(2026, 7, 15), date(2026, 7, 1), date(2026, 7, 31)),
    (date(2026, 2, 10), date(2026, 2, 1), date(2026, 2, 28)),
    (date(2024, 2, 10), date(2024, 2, 1), date(2024, 2, 29)),   # leap
    (date(2026, 12, 5), date(2026, 12, 1), date(2026, 12, 31)),
])
def test_month_bounds(day, first, last):
    assert report.month_bounds(day) == (first, last)


def test_month_bounds_walk_back_across_january():
    assert report.month_bounds(date(2026, 1, 15), back=1) == (
        date(2025, 12, 1), date(2025, 12, 31))


# ── config round trip ────────────────────────────────────────────────────
CONFIG = """\
# a comment that must survive
[meta]
project_roots = ["~/code"]
ignore = ["~/Desktop"]
checkpoint_questions = ["What shipped?"]

[projects."demo"]
path = "{path}"
status = "active"
phase = "building"

  [[projects."demo".checks]]
  name = "readme"
  type = "path"
  value = "README.md"
"""


def _write(tmp_path) -> Path:
    f = tmp_path / "progress.toml"
    f.write_text(CONFIG.format(path=tmp_path))
    return f


def test_load_parses_meta_and_projects(tmp_path):
    cfg = config.load(_write(tmp_path))
    assert [p.name for p in cfg.projects] == ["demo"]
    assert cfg.questions == ["What shipped?"]
    assert cfg.roots == [Path.home() / "code"]
    assert cfg.ignore == ["~/Desktop"]
    assert cfg.projects[0].checks[0].type == "path"


def test_saving_preserves_comments_and_structure(tmp_path):
    f = _write(tmp_path)
    cfg = config.load(f)
    cfg.set_answers("2026-07", ["shipped the thing"])
    config.save(cfg, f)
    text = f.read_text()
    assert "# a comment that must survive" in text
    assert config.load(f).answers_for("2026-07") == ["shipped the thing"]
    assert len(config.load(f).projects) == 1


def test_set_status_rejects_unknown_values(tmp_path):
    f = _write(tmp_path)
    cfg = config.load(f)
    cfg.set_status("demo", "nonsense")
    config.save(cfg, f)
    assert config.load(f).projects[0].status == "active"

    cfg.set_status("demo", "done")
    config.save(cfg, f)
    assert config.load(f).projects[0].status == "done"


def test_missing_config_is_empty_not_an_error(tmp_path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.projects == [] and cfg.questions == []


# ── transcript tailing ───────────────────────────────────────────────────
def _entry(**kw):
    kw.setdefault("timestamp", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    return json.dumps(kw)


def test_tail_survives_a_truncated_first_line(tmp_path):
    from mission_control.sessions import _tail_entries
    f = tmp_path / "s.jsonl"
    f.write_text("{ this is not valid json\n" + _entry(type="user") + "\n")
    entries = _tail_entries(f)
    assert [e["type"] for e in entries] == ["user"]


def test_tail_reads_only_the_window(tmp_path):
    from mission_control.sessions import _tail_entries
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(_entry(type="user", n=i) for i in range(500)) + "\n")
    assert len(_tail_entries(f, window=2_000)) < 500


# ── merge collisions ─────────────────────────────────────────────────────
def test_merge_never_replaces_a_longer_transcript_with_a_stub(tmp_path, monkeypatch):
    """Two slug dirs can hold the same session id: a live session keeps writing
    to its old slug after a move, regenerating a short stub of a transcript
    that also exists, far longer, in the new one. The stub has the *newer*
    mtime, so newest-wins would silently destroy real history."""
    import os
    from mission_control import reconcile

    store = tmp_path / "store"
    src = store / "-old-slug"
    dst = store / "-new-slug"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    name = "aaaa-bbbb.jsonl"
    (dst / name).write_text("\n".join(f'{{"n":{i}}}' for i in range(500)) + "\n")
    (src / name).write_text("\n".join(f'{{"n":{i}}}' for i in range(20)) + "\n")
    # the stub is the most recently written file
    os.utime(dst / name, (1, 1))

    monkeypatch.setattr(reconcile, "STORE", store)
    monkeypatch.setattr(reconcile, "ARCHIVE", tmp_path / "archive")
    ok, log = reconcile.apply([reconcile.Op("slug", src, dst)])

    surviving = sum(1 for _ in (dst / name).open())
    assert surviving == 500, f"history was truncated to {surviving} lines"
    assert ok, f"verification should pass: {log}"


def test_merge_keeps_files_unique_to_each_side(tmp_path, monkeypatch):
    from mission_control import reconcile
    store = tmp_path / "store"
    src, dst = store / "-a", store / "-b"
    src.mkdir(parents=True)
    dst.mkdir(parents=True)
    (src / "one.jsonl").write_text('{"a":1}\n')
    (dst / "two.jsonl").write_text('{"b":1}\n')
    monkeypatch.setattr(reconcile, "STORE", store)
    monkeypatch.setattr(reconcile, "ARCHIVE", tmp_path / "archive")
    ok, log = reconcile.apply([reconcile.Op("slug", src, dst)])
    assert {f.name for f in dst.glob("*.jsonl")} == {"one.jsonl", "two.jsonl"}
    assert ok, log
