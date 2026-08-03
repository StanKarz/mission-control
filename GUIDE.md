# mission control — guide

Everything in one place: what it is, the commands, the daily flow, and the
gotchas that cost real time to discover.

---

## What it actually is

Three things that happen to share a data source.

**1. A repair tool for Claude Code sessions.** Claude Code names its session
directory after the absolute path you launched from. Move the project and the
link silently breaks — history still on disk, unreachable. `doctor` finds it,
`fix` repairs it, `mv` prevents it.

**2. A progress tracker that can't lie.** Every checklist item is a *predicate*
the app evaluates, not a box you tick. A percentage is measured on every open,
so it can't drift from reality.

**3. A launcher.** Highlight a project, press `o`, and the resume command is
typed into the tmux pane on your left.

It reads two sources and owns neither:

| source | holds | who writes it |
|---|---|---|
| `~/.config/mission-control/progress.toml` | **intent** — what you're working on, what "done" means | you, and `mc new` / `/init-project` |
| `~/.claude/projects/` | **activity** — sessions, titles, edits | Claude Code. Read-only to us, except `fix`/`mv`/`retire` |

Plus `git log` for commits. **Nothing about activity is ever recorded in the
config** — it's derived every time.

---

## Commands

Everything that mutates is a **dry run unless you pass `--go`**, archives to
`~/.claude/archive/` first, and verifies transcript line counts after.

### Looking

```sh
mc                    # the TUI
mc doctor             # session-store health; exit 1 if anything is stranded
mc week      [n]      # what happened this week (or n weeks back)
mc month     [n]      # same, calendar month
mc recap     [days]   # sessions day by day
mc brief     [path]   # phase + next check for one project
```

### Doing

```sh
mc new <name>                    # mkdir + git init + config entry + launch claude
mc add [path]                    # track a directory that already exists (default: cwd)
mc check [project]               # run the cmd/gh_pr checks (see gotcha 7)
mc resume <project>              # start/resume in the left tmux pane
mc mv <project> <dest> --go      # move a project, carrying its sessions
mc fix --go                      # relink slugs stranded by a move done elsewhere
mc retire <project> --go         # drop a finished project out of --resume
mc unretire <project> --go       # ...and put it back
mc init                          # write a starter config
```

Useful flags: `--print` (show the command, don't run it), `--new-window`
(open in a fresh tmux window instead of the left pane), `--no-launch`
(`mc new` without dropping into Claude), `--hook` (`mc brief` as JSON for
`SessionStart`).

### Keys

**Roster**

| | |
|---|---|
| `j` `k` or arrows | move — the highlighted row expands |
| `g` `G` | top / bottom |
| `⏎` | open the detail page |
| `o` | resume in the left pane |
| `w` | resume in a new tmux window |
| `c` | run this project's `cmd`/`gh_pr` checks |
| `s` | change status (does not touch sessions) |
| `a` | reveal finished and parked projects |
| `m` | month checkpoint |
| `x` | retire (asks first) |
| `r` | refresh |
| `q` or `ctrl+c` | quit |

The arrow keys `up`/`down` work everywhere `j`/`k` do, and `home`/`end` alongside
`g`/`G`.

**Detail page** — `j`/`k` moves a cursor through the checks, commits and
sessions, and whatever is selected expands: a commit shows the files it touched,
a session shows which files it edited, a check explains why it isn't passing.
`⏎` resumes, `w` new window, `esc` back.

`q` and `ctrl+c` quit from anywhere. `esc` always goes back one screen.

---

## Flow

### Starting a project

```sh
mc new my-thing          # creates the dir, git init, adds it to progress.toml
                         # then drops you into claude
```

Already have the directory? `mc new` refuses to touch it — use **`mc add`**
instead, which takes the current directory by default:

```sh
cd ~/projects/existing-thing && mc add
```

The two are different steps, not alternatives: `mc new` / `mc add` make the
project *known* (a `[projects."name"]` block). `/init-project` defines what
*done* means (the checks). You need both.

Write a `CLAUDE.md` to scope it, then **`/init-project`** inside Claude. It
interviews you about the current phase and what would make it done, then writes
real checks into the config. It only does the phase you're actually on — future
phases are guesses, and guesses are what this replaces.

A one-off session that isn't a project? **Don't add it at all.** Unlisted means
invisible; `doctor` will note its sessions under `UNTRACKED`, which counts as
zero problems.

### A normal day

```sh
work        # splits tmux: work on the left, roster on the right
```

Glance at the roster. The highlighted row tells you the next unmet check.
Press `o` to resume that project — and the roster switches to that project's
detail, so the right-hand pane becomes its dashboard. `esc` goes back.

`o` builds the layout if it needs to. Running `mc` alone in a window? It opens a
work pane to the *left* at 60% and keeps itself on the right. If a left pane
already exists and is an idle shell, it types there instead. If Claude is
already running there it refuses — keystrokes would be submitted as a *prompt* —
and `w` opens a new window instead.

During a long run, the roster shows `◆ working 4m` or `◆ needs you`.

When you've done something a `cmd` check measures, press `c`. `path`, `git_tag`
and `manual` checks update by themselves.

### Weekly and monthly

```sh
mc week          # what got pushed, what you worked on, what went quiet
mc month
```

`m` in the roster opens the same month view, plus space to write answers to
`checkpoint_questions` if you've set any — editable only in the last three days
of the month, inert the rest of the time by design. `ctrl+s` saves,
`tab` moves between answers. `ctrl+s` saves, `tab` moves
between answers.

### When things move

Prefer `mc mv <project> <dest> --go` — it moves the directory *and* re-slugs
every session beneath it. If you moved something with plain `mv`, run
`mc doctor` then `mc fix --go`.

**Quit any Claude session in a project before moving it** (gotcha 2).

### When something is finished

Two separate things, and it's worth keeping them apart:

**`status = "done"`** is about the *roster*. Press **`s`** and pick it — the
project drops off the default view and lives behind `a`. That is already the
"completed projects" list; there is no separate menu because `a` is the menu.

**Retiring** (`x`, or `mc retire <name> --go`) is about *`claude --resume`*. It
marks the project done *and* moves its session directories to
`~/.claude/archive/shipped/<name>/` so they stop cluttering the session picker.
Nothing is deleted.

So: `s` when you just want to say what something is, `x` when you are finished
with it and want its sessions out of the way too. Marking done alone is enough
to tidy the roster — a finished project with twenty sessions is the one worth
retiring as well.

```
s 4        mark it done; it moves behind `a`
a          look at everything you have finished
x          done *and* archive the sessions (asks first)
```

**`mc unretire <name> --go`** is the exact inverse: it restores every slug
directory, including nested sub-repos, and sets the status back to `active`.

---

## Statuses

| status | meaning | on the roster? |
|---|---|---|
| `active` | working on it | yes |
| `blocked` | waiting on something external (a PR, a review) | yes |
| `paused` | not touching it now, but coming back | yes, quieter |
| `done` | finished | behind `a` |
| `archived` | filed away, not coming back soon | behind `a` |
| `ignored` | not a project at all | never |

**`paused` vs `archived`** is the distinction worth getting right. Paused work
is still yours — it stays on the roster so you don't forget it exists, just
rendered quietly and sorted below live work. Archived work is filed away and
only appears under `a`.

`ignored` still keeps its sessions *tracked*, so `doctor` won't report them as
orphans. That's the difference between "stop showing me this" and "this was
never mine".

---

## Gotchas

Each of these cost real time. Most are properties of Claude Code, not of this
app.

**1. Slug encoding is lossy — never decode it.** `/`, `_` and `.` all become
`-`, so `-Users-you-my-app` could be `my/app`, `my-app` or `my_app`. Always
encode *forwards* from a known path; recover unknown paths from the `cwd` field
inside a transcript.

**2. A live session pins its slug for the whole process lifetime.** Claude Code
captures its cwd *string* at session start and never re-reads it. Move a project
under a running session and it keeps appending to the old, now-stranded slug.
Checking with `ps` doesn't work (cwd isn't on the command line) and `lsof` lies
(a process's cwd follows the inode, so it reports the *new* path). The reliable
signal is a recent write. `mv`, `fix` and `retire` all refuse on live slugs.

**3. `--resume` selects by directory alone.** A transcript whose internal `cwd`
points elsewhere still resumes fine from whichever slug directory it sits in.
That's what makes repair a plain `mv`. Mixed `cwd` values in one transcript are
normal, not damage.

**4. `mtime` is not evidence of work.** It records when a file was last
*written* — which a move, a copy or a backup all do. Filter on the `timestamp`
values inside, or a recap reports yesterday's session as today's.

**5. Two slug dirs can hold the same session id, with divergent content.** When
a live session writes to its old slug after a move, you get two continuations of
one session where **neither contains the other**. Resolving that by size or
mtime silently destroys whichever loses. `fix`/`mv` detect it, report
`CONFLICT`, and leave both files alone.

**6. macOS is case-insensitive.** `~/Projects/x` and `~/projects/x` are one
directory with two different slugs. Renaming one to the other is a rename onto
itself and needs a temp hop, or a naive implementation destroys data.

**7. `cmd` and `gh_pr` checks don't run by themselves.** They shell out or hit
the network, so they never run in a render path — otherwise the app would stall
every few seconds. Press `c` or run `mc check`. Results are cached and count
towards the percentage, with the last-run time shown. A check that's never been
run reports as unresolved, never as failing, so the number never overstates.

**8. `mc` discovers nothing.** The roster is exactly what `progress.toml` lists.
`project_roots` only feeds `mc fix` (searching for moved projects) and `mc new`.

**9. A project's sessions aren't one directory.** A sub-repo inside a project
gets its own slug. `mc mv` re-slugs the whole subtree; a naive move would
strand the nested one.

**10. `SessionStart` hooks need a specific JSON envelope.** Plain stdout is
silently dropped — no error, just nothing. `mc brief --hook` emits the correct
`hookSpecificOutput.additionalContext` shape.

**11. Sending keys to a busy tmux pane is prompt injection.** If Claude is
running in the target pane, a resume command typed there is submitted as a
*prompt*, not executed. `mc` checks `pane_current_command` against a shell
allowlist and refuses otherwise, offering `w` instead.

**11a. tmux resolves `{left-of}` against the _active_ pane, not the asking
one.** Usually the same pane, but not after `o` moves focus to the new work
pane — a later query then answers relative to the wrong pane, and can wrap
around to point back at the roster itself. `mc` reads the pane layout and picks
the nearest pane whose right edge touches its own, which has no such ambiguity.

**12. `uv tool install --force` can silently do nothing.** It skips the rebuild
when the version string is unchanged, so a "reinstall" leaves the old code in
place and any fix appears not to work. This repo is installed with
`uv tool install --editable .`, so `mc` always runs the current source. If you
ever reinstall it non-editable, bump `version` in `pyproject.toml` first.

**13. `ai-title` is a free summary.** Claude Code writes its own session title
into the transcript, so nothing here needs an LLM to summarise what you did.

---

## Where things live

```
~/.config/mission-control/progress.toml   config (symlinked to ~/dotfiles)
~/.claude/projects/<slug>/*.jsonl         sessions — read-only to us
~/.claude/archive/                        everything fix/mv/retire moved
~/.cache/mission-control/                 parsed-session and check caches
~/.claude/skills/init-project/            the /init-project skill
```

Caches are disposable — delete them and they rebuild.

---

## Development

```sh
uv run pytest        # 70 tests, ~5s
```

Tests are weighted towards where a bug is silent and expensive: slug encoding,
reconcile planning, the checks engine, period arithmetic, tmux target
resolution. There are also smoke
tests that render every screen and exercise the key map — added after a
`NameError` in the detail view got shipped, which pure-logic tests structurally
could not catch.

New behaviour worth trusting gets verified by *breaking it on purpose* and
confirming a test fails.
