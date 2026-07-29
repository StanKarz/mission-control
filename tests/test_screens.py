"""Smoke tests: every screen must actually render, and keys must do one thing.

The pure-logic tests missed a NameError in the detail screen's activity block
because nothing ever composed it. Rendering is cheap to assert and catches a
whole class of bug that unit tests structurally cannot.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

CONFIG = """\
[meta]
project_roots = ["{root}"]
ignore = []
checkpoint_questions = ["What shipped?", "What stalled?"]

[projects."alpha"]
path = "{root}/alpha"
status = "active"
phase = "building"

  [[projects."alpha".checks]]
  name = "readme"
  type = "path"
  value = "README.md"

  [[projects."alpha".checks]]
  name = "tagged"
  type = "manual"
  done = false

[projects."beta"]
path = "{root}/beta"
status = "blocked"
phase = "awaiting review"

[projects."gamma"]
path = "{root}/gamma"
status = "archived"
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    for name in ("alpha", "beta", "gamma"):
        (root / name).mkdir(parents=True)
    (root / "alpha" / "README.md").write_text("hi")

    cfg = tmp_path / "progress.toml"
    cfg.write_text(CONFIG.format(root=root))
    monkeypatch.setenv("MC_CONFIG", str(cfg))

    # isolate from the real session store and cache
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr("mission_control.sessions.STORE", store)
    monkeypatch.setattr("mission_control.sessions.CACHE", tmp_path / "cache.json")
    monkeypatch.setattr("mission_control.reconcile.STORE", store)

    import mission_control.config as config
    monkeypatch.setattr(config, "PATH", cfg)

    from mission_control.app import MissionControl
    return MissionControl()


async def test_roster_renders(app):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        assert [p.name for p in app.screen.rows] == ["alpha", "beta", "gamma"]


async def test_detail_renders_for_every_project(app):
    """Would have caught the undefined `hi` in the activity block."""
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        for i in range(len(app.screen.rows)):
            app.screen.index = i
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "Detail"
            await pilot.press("escape")
            await pilot.pause()


async def test_checkpoint_renders(app):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert type(app.screen).__name__ == "Checkpoint"


async def test_arrow_keys_move_the_selection_not_the_scrollbar(app):
    """The scroll container used to hold focus and eat up/down as scrolling."""
    async with app.run_test(size=(76, 16)) as pilot:
        await pilot.pause()
        r = app.screen
        assert r.index == 0
        await pilot.press("down")
        await pilot.pause()
        assert r.index == 1, "arrow key did not move the selection"
        await pilot.press("up")
        await pilot.pause()
        assert r.index == 0


@pytest.mark.parametrize("keys", [["q"], ["ctrl+c"], ["enter", "q"], ["c", "q"]])
async def test_q_and_ctrl_c_quit_from_anywhere(app, keys):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        for k in keys:
            await pilot.press(k)
            await pilot.pause()
        assert not app.is_running


async def test_escape_goes_back_rather_than_quitting(app):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.is_running
        assert type(app.screen).__name__ == "Roster"


async def test_typing_q_into_the_checkpoint_box_does_not_quit(app, monkeypatch):
    import mission_control.modals as modals
    monkeypatch.setattr(modals, "is_open", lambda today=None, window=3: True)
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        box = app.screen.areas[0]
        box.focus()
        await pilot.pause()
        for ch in "quit":
            await pilot.press(ch)
        await pilot.pause()
        assert box.text == "quit"
        assert app.is_running


async def test_progress_shows_no_percentage_when_nothing_is_measured(app):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        r = app.screen
        alpha = r._row(r.rows[0], False)     # has 2 checks, 1 passing
        beta = r._row(r.rows[1], False)      # has none
        assert "50%" in alpha
        assert "%" not in beta, "a project with no checks must not report a percentage"
