# mission control — guide

If you read one section, read [Flow](#flow). That's the daily loop.

The rest is reference you can come back to: [commands](#commands),
[keys](#keys), [statuses](#statuses), and the [gotchas](#gotchas) that cost real
time to find.

---

## What it actually is

One tool to manage several projects at once, answering: what am I working on, how
far through is each one, what's next, and is anything running right now.

Three parts make that work.

**A progress tracker that can't lie.** Every checklist item is a *predicate* the
app evaluates, not a box you tick, so the percentage is measured on every open
and can't drift.

**A launcher.** Highlight a project, press `o`, and the resume command is typed
into the tmux pane on your left.

**A repair tool, when you need one.** Claude Code names each session directory
after the absolute path you launched from, so moving a project silently breaks
the link. `doctor` finds it, `fix` repairs it, `mv` prevents it.

It reads two sources and owns neither:

| source                                      | holds                                                         | who writes it                                                 |
| ------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------- |
| `~/.config/mission-control/progress.toml` | **intent**: what you're working on, what "done" means | you, and `mc new` / `/init-project`                        |
| `~/.claude/projects/`                     | **activity**: sessions, titles, edits                 | Claude Code. Read-only to us, except `fix`/`mv`/`retire` |

Plus `git log` for commits. **Nothing about activity is ever recorded in the
config.** It's derived every time.

---

## Commands

`mc --help` lists them all. Two rules cover most of the surface:

- Anything that mutates is a **dry run unless you pass `--go`**. It archives to
  `~/.claude/archive/` first and verifies transcript line counts after.
- `mc` with no arguments opens the TUI. Everything else prints and exits, so it
  composes with pipes and shell startup files.

The four you'll actually type daily:

```sh
mc                    # the roster
mc week               # what happened this week
mc check [project]    # run the cmd/gh_pr checks the TUI skips
mc add                # track the directory you're standing in
```

Useful flags: `--print` (show the command, don't run it), `--new-window` (open
in a fresh tmux window instead of the left pane), `--no-launch` (`mc new`
without dropping into Claude), `--hook` (`mc brief` as JSON for `SessionStart`).

### Keys

**Roster**

|                       |                                            |
| --------------------- | ------------------------------------------ |
| `j` `k` or arrows | move; the highlighted row expands         |
| `g` `G`           | top / bottom                               |
| `⏎`                | open the detail page                       |
| `o`                 | resume in the left pane                    |
| `w`                 | resume in a new tmux window                |
| `c`                 | run this project's `cmd`/`gh_pr` checks |
| `s`                 | change status (does not touch sessions)    |
| `a`                 | reveal finished and parked projects        |
| `m`                 | month checkpoint                           |
| `x`                 | retire (asks first)                        |
| `r`                 | refresh                                    |
| `q` or `ctrl+c`   | quit                                       |

The arrow keys `up`/`down` work everywhere `j`/`k` do, and `home`/`end` alongside
`g`/`G`.

**Detail page**

|                       |                                                    |
| --------------------- | -------------------------------------------------- |
| `j` `k` or arrows | move a cursor through checks, commits and sessions |
| `space`             | tick the selected `manual` check off, or back on  |
| `⏎`                | resume                                             |
| `w`                 | resume in a new tmux window                        |
| `esc`               | back                                               |

Whatever is selected expands: a commit shows the files it touched, a session
shows which files it edited, a check explains why it isn't passing.

`q` and `ctrl+c` quit from anywhere. `esc` always goes back one screen.

---

## Flow

### Starting a project

```sh
mc new my-thing          # creates the dir, git init, adds it to progress.toml
                         # then drops you into claude
```

Already have the directory? `mc new` refuses to touch it. Use **`mc add`**
instead, which takes the current directory by default:

```sh
cd ~/projects/existing-thing && mc add
```

The two are different steps, not alternatives: `mc new` / `mc add` make the
project *known* (a `[projects."name"]` block). `/init-project` defines what
*done* means (the checks). You need both.

Write a `CLAUDE.md` to scope it, then **`/init-project`** inside Claude. It
works out what *done* means for the phase you're on and writes it into the
config as checks the app can evaluate. It covers the current phase only, since
future phases are guesses and guesses are what this replaces.

A one-off session that isn't a project? **Don't add it at all.** Unlisted means
invisible; `doctor` will note its sessions under `UNTRACKED`, which counts as
zero problems.

### A normal day

```sh
mc work     # your shell stays here, the roster opens beside it
```

Worth a shell alias: `work() { mc work; }`.

You get two panes either way. In a fresh window it splits; in a window you had
already split by hand it uses the pane that's there rather than adding a third.
If something other than a shell is in that pane it refuses, for the same reason
`o` does.

Glance at the roster. The highlighted row tells you the next unmet check.
Press `o` to resume that project, and the roster switches to that project's
detail, so the right-hand pane becomes its dashboard. `esc` goes back.

`o` builds the layout if it needs to. Running `mc` alone in a window? It opens a
work pane to the *left* at 60% and keeps itself on the right. If a left pane
already exists and is an idle shell, it types there instead. If Claude is
already running there it refuses, since keystrokes would be submitted as a
*prompt*, and `w` opens a new window instead.

During a long run, the roster shows `◆ working 4m` or `◆ needs you`.

When you've done something a `cmd` check measures, press `c`. `path` and
`git_tag` checks re-evaluate on every open, so they need nothing.

For the things nothing can measure, an outline reorganised or a decision made,
use a `manual` check and tick it off by hand: `⏎` into the project, `j`/`k` to
the check, `space`. Manual checks render as `☐` / `☑` rather than `✔` / `✖`, so
you can see at a glance which rows are yours to tick and which are measured.
`space` deliberately refuses to touch the other four types (gotcha 2).

### Adding a check

Checks are plain TOML, so the quickest way to add one is to write it:

```toml
[[projects."robo-scholar".checks]]
name  = "eval set reviewed"
type  = "manual"
done  = false
```

A `manual` check needs those three fields. The other four types take `value`
instead of `done`, holding the path, tag, shell command or PR. Press `r` in the
roster to pick it up.

Re-running **`/init-project`** is the other route and the better one when the
phase has moved on. It appends rather than replaces, and it's meant to be run
once per phase rather than once per project.

Aim for three to six checks per phase. Fewer and the percentage is too coarse to
mean anything, more and it's busywork.

### Weekly and monthly

```sh
mc week          # what got pushed, what you worked on, what went quiet
mc month
```

`m` in the roster opens the same month view, plus space to write answers to
`checkpoint_questions` if you've set any, editable only in the last three days
of the month and inert the rest of the time, by design. `ctrl+s` saves, `tab` moves
between answers.

### When things move

Prefer `mc mv <project> <dest> --go`. It moves the directory *and* re-slugs
every session beneath it. If you moved something with plain `mv`, run
`mc doctor` then `mc fix --go`.

**Quit any Claude session in a project before moving it** (gotcha 4).

### When something is finished

Two separate things, and it's worth keeping them apart:

**`status = "done"`** is about the *roster*. Press **`s`** and pick it: the
project drops off the default view and lives behind `a`. That is already the
"completed projects" list; there is no separate menu because `a` is the menu.

**Retiring** (`x`, or `mc retire <name> --go`) is about *`claude --resume`*. It
marks the project done *and* moves its session directories to
`~/.claude/archive/shipped/<name>/` so they stop cluttering the session picker.
Nothing is deleted.

So: `s` when you just want to say what something is, `x` when you are finished
with it and want its sessions out of the way too. Marking done alone is enough
to tidy the roster; a finished project with twenty sessions is the one worth
retiring as well.

```
s 4        mark it done; it moves behind `a`
a          look at everything you have finished
x          done *and* archive the sessions (asks first)
```

With `a` on, retiring does not make the row disappear, since `a` is the view
that shows finished work, so a project you just finished belongs in it. What changes
is the status, and the row sorts down into the done group. The cursor follows
it, and the notification says which of the two halves happened: a project with
no sessions on disk reports `no sessions to archive`.

**`mc unretire <name> --go`** is the exact inverse: it restores every slug
directory, including nested sub-repos, and sets the status back to `active`.

### When something is deleted

Different from finished, and `x` is the wrong key for it. Retiring moves
sessions to `~/.claude/archive/`, which sits outside Claude Code's retention
sweep, so it keeps them for good: right for a project you might reopen,
backwards for one you are binning.

Tracked project:

```sh
rm -rf ~/Desktop/projects/<name>
# then delete its [projects."<name>"] block from progress.toml
```

Dropping the block is the step that matters. Leave it in and `doctor` prints
`! <name> path missing` on every run and the roster shows it in amber under `a`.

An untracked one-off needs neither step: delete the directory and stop. Claude
Code deletes the transcripts after `cleanupPeriodDays` (30 by default), so the
slug goes by itself. Until then `doctor` lists it under STRANDED and exits 1.
That is noise rather than a problem, and `mc fix` declines to guess where it went.

To clear one sooner, `claude project purge ~/Desktop/projects/<name>` deletes
the transcripts, the matching prompt history and the trust entry. It prints a
plan and asks first, and `--dry-run` shows the plan without touching anything.
Unlike everything `mc` does, it cannot be undone.

---

## Statuses

| status       | meaning                                        | on the roster? |
| ------------ | ---------------------------------------------- | -------------- |
| `active`   | working on it                                  | yes            |
| `blocked`  | waiting on something external (a PR, a review) | yes            |
| `paused`   | not touching it now, but coming back           | yes, quieter   |
| `done`     | finished                                       | behind `a`   |
| `archived` | filed away, not coming back soon               | behind `a`   |
| `ignored`  | not a project at all                           | never          |

**`paused` vs `archived`** is the distinction worth getting right. Paused work
is still yours, so it stays on the roster where you won't forget it exists, just
rendered quietly and sorted below live work. Archived work is filed away and
only appears under `a`.

`ignored` still keeps its sessions *tracked*, so `doctor` won't report them as
orphans. That's the difference between "stop showing me this" and "this was
never mine".

---

## Gotchas

Each of these cost real time, and most are properties of Claude Code rather than
of this app.

**1. `cmd` and `gh_pr` checks don't run by themselves.** They shell out or hit
the network, so they never run in a render path; the app would stall every few
seconds. Press `c`, or run `mc check`. Results are cached and count towards the
percentage, with the last-run time shown. A check that has never run reports as
unresolved rather than failing, so the number never overstates.

**2. `space` only ticks `manual` checks.** Overriding a `path` or `cmd` check by
hand would leave a stored answer sitting beside a predicate that disagrees with
it, and no way to tell later which one you believed. If a check keeps being
wrong, fix the predicate or change its `type` to `manual` deliberately.

**3. `mc` discovers nothing.** The roster is exactly what `progress.toml` lists.
`project_roots` only feeds `mc fix`, when it searches for a project that moved,
and `mc new`.

**4. Anything that mutates refuses while a session is live.** `mv`, `fix` and
`retire` all check for recent writes first. A running session keeps writing to
its old slug after a move, so repairing it mid-flight just gets undone and
splits the transcript across two places.

**5. Two slug dirs can hold the same session id, with divergent content.** When
that happens neither file contains the other, and resolving it by size or date
destroys whichever loses. `fix` and `mv` report `CONFLICT` and leave both alone
for you to sort out by hand.

**6. Sending keys to a busy tmux pane is prompt injection.** If Claude is
running in the target pane, a resume command typed there is submitted as a
*prompt* rather than executed. `mc` checks the pane's foreground command against
a shell allowlist and refuses otherwise, offering `w` instead. Claude Code sets
its process title to its own version, so tmux calls that pane `2.1.284`; `mc`
translates it back to `claude` in the message.

**7. `mc resume` only works from the roster pane.** It types into the pane on
its *left*, so running it in the work pane itself reports `no pane to the left`.
You're in a shell there already, so run the command it prints.

**8. `SessionStart` hooks need a specific JSON envelope.** Plain stdout is
silently dropped: no error, just nothing. `mc brief --hook` emits the correct
`hookSpecificOutput.additionalContext` shape.

**9. `uv tool install --force` can silently do nothing.** It skips the rebuild
when the version string is unchanged, so a reinstall leaves the old code in place
and your fix appears not to work. This repo is installed with
`uv tool install --editable .` so `mc` always runs current source.

**10. Transcripts are deleted after 30 days.** `cleanupPeriodDays` defaults to
30, keyed on last activity, which is why `mc month 2` can come back empty.
`~/.claude/archive/` is outside the sweep, so retiring a project keeps its
sessions indefinitely.

---

## Where things live

```
~/.config/mission-control/progress.toml   config (keep it in your dotfiles)
~/.claude/projects/<slug>/*.jsonl         sessions, read-only to us
~/.claude/archive/                        everything fix/mv/retire moved
~/.cache/mission-control/                 parsed-session and check caches
~/.claude/skills/init-project/            the /init-project skill
```

Caches are disposable: delete them and they rebuild.

---

## Development

Running the tests and how they're weighted: see
[the README](README.md#development).
