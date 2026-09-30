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

[projects."delta"]
path = "{root}/delta"
status = "paused"
phase = "on ice"
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    for name in ("alpha", "beta", "gamma", "delta"):
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
    # isolate the slow-check result cache too, or tests read real results
    monkeypatch.setattr("mission_control.checks.CACHE", tmp_path / "checks.json")

    import mission_control.config as config
    monkeypatch.setattr(config, "PATH", cfg)

    from mission_control.app import MissionControl
    return MissionControl()


async def test_roster_hides_finished_and_parked_work_by_default(app):
    """alpha is active, beta blocked, gamma archived. Only work in flight
    should be on screen; the rest is one keypress away."""
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        # delta is paused: still yours, so still on the roster.
        # gamma is archived: filed away, so not.
        assert [p.name for p in app.screen.rows] == ["alpha", "beta", "delta"]
        assert app.screen.hidden_count == 1


async def test_a_reveals_everything(app):
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert [p.name for p in app.screen.rows] == ["alpha", "beta", "delta", "gamma"]
        await pilot.press("a")
        await pilot.pause()
        assert [p.name for p in app.screen.rows] == ["alpha", "beta", "delta"]


async def test_detail_renders_for_every_project(app):
    """Would have caught the undefined `hi` in the activity block."""
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")      # include archived, so every kind renders
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
        await pilot.press("m")
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


@pytest.mark.parametrize("keys", [["q"], ["ctrl+c"], ["enter", "q"], ["m", "q"]])
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
        await pilot.press("m")
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


async def test_c_runs_slow_checks_and_caches_the_result(app, tmp_path):
    """cmd checks are unreachable from any render path, so without this key
    a project whose checks are mostly cmd reports a permanent zero."""
    from mission_control import checks, config
    cfg = config.load()
    alpha = next(p for p in cfg.projects if p.name == "alpha")
    alpha.checks.append(config.Check(name="always ok", type="cmd", value="true"))

    assert checks.progress(alpha)[0] == 1          # readme only; cmd unresolved
    checks.run_all_slow(alpha)
    assert checks.cached(alpha)["always ok"] is True
    assert checks.progress(alpha)[0] == 2, "cached slow result must count"
    assert checks.cached_at(alpha) is not None


async def test_o_builds_the_layout_when_the_roster_is_alone(app, monkeypatch):
    """Pressing resume in a single-pane window should create the work pane,
    not refuse. Refusing was the reported bug."""
    from mission_control import launcher
    calls = []
    monkeypatch.setattr(launcher, "resolve_target",
                        lambda spec=None: launcher.Target(
                            problem="no pane to the left", reason="no-pane"))
    monkeypatch.setattr(launcher, "split_for_work",
                        lambda path, cmd, width="60%": (
                            calls.append((str(path), cmd)), (True, "opened"))[1])
    monkeypatch.setattr(launcher, "send",
                        lambda t, c: (_ for _ in ()).throw(
                            AssertionError("must not send to a pane that isn't there")))

    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert len(calls) == 1, "no work pane was created"
        path, cmd = calls[0]
        assert path.endswith("alpha")
        assert cmd.startswith("claude")


async def test_o_drills_into_the_project_after_launching(app, monkeypatch):
    from mission_control import launcher
    monkeypatch.setattr(launcher, "resolve_target",
                        lambda spec=None: launcher.Target(pane="%9", command="zsh"))
    monkeypatch.setattr(launcher, "send", lambda t, c: (True, "sent"))
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert type(app.screen).__name__ == "Detail"


async def test_a_busy_neighbour_neither_sends_nor_splits(app, monkeypatch):
    from mission_control import launcher
    monkeypatch.setattr(launcher, "resolve_target",
                        lambda spec=None: launcher.Target(
                            pane="%9", command="claude", reason="busy",
                            problem="claude is running there"))
    monkeypatch.setattr(launcher, "send",
                        lambda t, c: (_ for _ in ()).throw(
                            AssertionError("would inject a prompt into Claude")))
    monkeypatch.setattr(launcher, "split_for_work",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("would fragment the window")))
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert type(app.screen).__name__ == "Roster"


def test_mc_new_binds_its_config(tmp_path, monkeypatch):
    """`mc new` referenced an unbound `cfg` and raised NameError for every
    invocation between the de-personalisation commit and now. Nothing caught
    it because no test ever called the command."""
    import mission_control.config as config
    from mission_control.__main__ import cmd_new

    cfg_path = tmp_path / "p.toml"
    root = tmp_path / "projects"
    root.mkdir()
    cfg_path.write_text(f'[meta]\nproject_roots = ["{root}"]\nignore = []\n')
    monkeypatch.setattr(config, "PATH", cfg_path)
    monkeypatch.setenv("MC_CONFIG", str(cfg_path))

    assert cmd_new("fresh", launch=False) == 0
    assert (root / "fresh").is_dir()
    assert '[projects."fresh"]' in cfg_path.read_text()
    # a second call must refuse rather than clobber
    assert cmd_new("fresh", launch=False) == 2


def test_mc_add_adopts_an_existing_directory(tmp_path, monkeypatch):
    import mission_control.config as config
    from mission_control.__main__ import cmd_add

    cfg_path = tmp_path / "p.toml"
    root = tmp_path / "projects"
    (root / "legacy").mkdir(parents=True)
    cfg_path.write_text(f'[meta]\nproject_roots = ["{root}"]\nignore = []\n')
    monkeypatch.setattr(config, "PATH", cfg_path)
    monkeypatch.setenv("MC_CONFIG", str(cfg_path))

    assert cmd_add(str(root / "legacy")) == 0
    cfg = config.load(cfg_path)
    assert [p.name for p in cfg.projects] == ["legacy"]
    assert cmd_add(str(root / "legacy")) == 2, "must not add the same dir twice"
    assert cmd_add(str(root / "nope")) == 2, "must reject a missing directory"


async def test_s_changes_status_without_touching_sessions(app, monkeypatch):
    """`x` couples retire and done. `s` is the other half: say what a project
    *is* without archiving anything."""
    from mission_control import config, reconcile
    monkeypatch.setattr(reconcile, "retire",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("status change must not retire")))
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        r = app.screen
        assert r.rows[0].name == "alpha" and r.rows[0].status == "active"
        await pilot.press("s")
        await pilot.pause()
        assert type(app.screen).__name__ == "StatusPicker"
        await pilot.press("4")            # done
        await pilot.pause()
        assert config.load().projects[0].status == "done"


async def test_status_picker_cancels_cleanly(app):
    from mission_control import config
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert type(app.screen).__name__ == "Roster"
        assert config.load().projects[0].status == "active", "cancel must not write"


async def test_marking_done_removes_it_from_the_default_view(app):
    from mission_control import config
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        r = app.screen
        assert "alpha" in [p.name for p in r.rows]
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("4")            # done
        await pilot.pause()
        assert "alpha" not in [p.name for p in app.screen.rows]
        await pilot.press("a")            # ...but still there under `a`
        await pilot.pause()
        assert "alpha" in [p.name for p in app.screen.rows]


async def test_space_ticks_a_manual_check_and_moves_the_percentage(app):
    """alpha has two checks: readme (path, passing) and tagged (manual, false).
    Ticking the manual one has to move the number, the roster and the file —
    a toggle that only changes the glyph is a toggle you cannot trust."""
    from mission_control import config
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        roster = app.screen
        assert "50%" in roster._row(roster.rows[0], False)

        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert type(detail).__name__ == "Detail"
        await pilot.press("j")              # cursor 0 = readme, 1 = tagged
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

        assert detail.p.checks[1].done is True
        assert config.load().projects[0].checks[1].done is True, "not persisted"
        assert detail.query_one("#pct").value == "100"

        await pilot.press("escape")
        await pilot.pause()
        # what is painted, not what _row would recompute — the roster has to
        # repaint on the way back, not on its next four-second tick
        assert "100" in str(app.screen.row_widgets[0].content)

        # and back off again
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("space")
        await pilot.pause()
        assert config.load().projects[0].checks[1].done is False


async def test_space_leaves_measured_checks_alone(app):
    """Overriding a path check would leave the predicate disagreeing forever."""
    from mission_control import config
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        before = Path(os.environ["MC_CONFIG"]).read_text()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("space")          # cursor is on readme, a path check
        await pilot.pause()
        assert app.screen.p.checks[0].done is False
        assert Path(os.environ["MC_CONFIG"]).read_text() == before
        assert config.load().projects[0].checks[0].done is False


async def test_space_on_a_project_with_no_checks_does_nothing(app):
    """beta has no checks at all, so there is no cursor and nothing to tick."""
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        app.screen.index = 1                # beta
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert app.is_running
        assert type(app.screen).__name__ == "Detail"


def _notes(screen, monkeypatch) -> list[str]:
    """Capture what the screen told the user."""
    said: list[str] = []
    monkeypatch.setattr(type(screen), "notify",
                        lambda self, msg, **kw: said.append(str(msg)))
    return said


async def test_retiring_in_show_all_keeps_the_cursor_on_that_project(app, monkeypatch):
    """With `a` on, a retired project stays on the roster (done is shown) but
    re-sorts into the done group. The index used to stay put, so the highlight
    silently jumped to a different project and it read as "nothing happened"."""
    from mission_control import reconcile
    monkeypatch.setattr(reconcile, "slugs_under", lambda p: [])
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        r = app.screen
        assert [p.name for p in r.rows] == ["alpha", "beta", "delta", "gamma"]
        r.index = 1                                  # beta, blocked
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert r.rows[r.index].name == "beta", "cursor must follow the project"
        assert [p.name for p in r.rows] == ["alpha", "delta", "beta", "gamma"]
        assert next(p for p in r.rows if p.name == "beta").status == "done"


async def test_retire_says_whether_any_sessions_moved(app, monkeypatch):
    """"retired beta" with nothing archived reads as a no-op when the row also
    stays put. Say which half actually happened."""
    from mission_control import reconcile
    monkeypatch.setattr(reconcile, "slugs_under", lambda p: [])
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        said = _notes(app.screen, monkeypatch)
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert said, "retiring said nothing at all"
        assert "done" in said[-1]
        assert "no sessions" in said[-1]


async def test_changing_status_keeps_the_cursor_on_that_project(app):
    """Same re-sort problem behind `s`."""
    async with app.run_test(size=(76, 30)) as pilot:
        await pilot.pause()
        r = app.screen
        r.index = 0                                  # alpha, active
        await pilot.press("a")                       # show everything
        await pilot.pause()
        r.index = 0
        await pilot.press("s")
        await pilot.pause()
        await pilot.press("4")                       # done
        await pilot.pause()
        assert r.rows[r.index].name == "alpha"
        assert r.rows[r.index].status == "done"
