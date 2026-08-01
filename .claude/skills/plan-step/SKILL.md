---
name: plan-step
description: Implements the next unfinished step in this project's plan file, marking it done when complete. Invoked by the overnight autopilot script — not meant to trigger automatically during normal interactive work.
disable-model-invocation: true
---

`$ARGUMENTS` is the path to this project's plan file.

You are running unattended, with no one available to answer questions.
Make reasonable, conservative decisions and keep going rather than stalling —
but see rule 4 for when to stop instead of guessing.

## Rules

1. **Pick the step**: Read the plan file at `$ARGUMENTS`. It documents many
   steps, marked done by a `— **done**` suffix on the heading once finished
   (both `## Step N — ...` headings and lettered sub-steps like
   `### Na. ...`). Find the next step or sub-step that is **not** marked
   done. Usually that's the first undone one in document order, but read the
   surrounding prose carefully first — some steps explain a dependency or
   ordering reason (e.g. "2e moved first because...") that means a
   later-numbered sub-step should actually go before an earlier-numbered
   one. Match that reasoning rather than blindly going top-to-bottom. Ignore
   non-step headings (decisions, "already copied" notes, target-layout
   listings) — only headings that are actual numbered steps or sub-steps
   count.

   If every step in the file is already marked done, print exactly the line
   `NO_PENDING_STEPS`, make no changes, and stop.

   Otherwise, before making any other change, create and check out a branch
   for this step: `git checkout -b <branch-name>`. The branch name must
   start with `autopilot/` — autopilot relies on that prefix to recognize
   an abandoned step branch and refuse to treat it as a base branch. Use
   `autopilot/$(date -u +%Y%m%d-%H%M%S)-<short-slug-of-the-step>`, actually
   running `date` rather than writing a timestamp from memory; the
   timestamp keeps a blocked branch from an earlier run (rule 4) from
   colliding with a new attempt.

   Confirm the checkout actually succeeded (`git branch --show-current`)
   before going further. If it failed for any reason, stop immediately per
   rule 4 — do **not** start editing files on the base branch. Autopilot
   verifies this too and will refuse to commit if the repo isn't on the
   branch you report.

   Once it succeeds, print exactly one line, with nothing else on it:
   `AUTOPILOT_BRANCH=<branch-name>`. This is the only way autopilot learns
   which branch to merge afterward — if it's missing, the run stops for
   human review.

   Implement exactly what the chosen step describes. Don't start on any
   other step, and don't expand or reinterpret its scope beyond what it
   says. Do one step (or one sub-step) per session, even if a neighboring
   one looks related — each session, and its branch, covers exactly one.

   **The three sentinel lines** (`NO_PENDING_STEPS`,
   `AUTOPILOT_BRANCH=<name>`, `HUMAN_REVIEW_REQUIRED`) are matched as whole
   lines. Each must appear alone on its own line, exactly, with no
   surrounding prose, quotes, or backticks. You may discuss them in your
   narration — a mention inside a sentence won't be mistaken for the real
   thing — but only emit one as a bare line when you actually mean it.

2. **Tests**: Run this project's checks before you finish and make sure
   they pass, fixing any failures your changes introduced. Prefer the
   commands the project documents (in this repo, CLAUDE.md specifies
   `make test` and `make lint`, and requires lint to be clean before
   anything is committed). If the repo has a `./run_tests.sh`, that is the
   same gate autopilot will run after you finish, so running it yourself
   first is the surest way to know your step will actually land.

3. **No further git**: Other than the one `git checkout -b` from rule 1,
   do not run `git add`, `git commit`, `git merge`, or switch branches
   again. The autopilot script handles staging, committing, merging your
   branch back into the base branch, and deleting it, after this session
   ends — based on whether the working tree has changes and (if present)
   whether tests pass.

4. **When you're genuinely blocked**: if the step is ambiguous in a way that
   materially changes the outcome, conflicts with existing code, or depends
   on something missing (a credential, a library decision, a prior step that
   wasn't actually done), stop rather than guessing. Don't revert whatever
   changes you've made so far — they may be unrelated to what's blocking
   you and still worth keeping for a human to pick up from. Just don't mark
   the step done or write an Implementation Summary for it, since it didn't
   succeed; a bullet in `## Unresolved Issues` (rule 6) noting the blocker
   is fine and encouraged.

   Clearly explain what's blocking you and what you'd need to proceed, then
   print exactly the line `HUMAN_REVIEW_REQUIRED` and stop. Autopilot
   doesn't infer this from your exit code or from whether the working tree
   changed — whatever files you touched stay uncommitted, on your step
   branch, either way — this line is the only signal it watches for to halt
   the run without testing, merging, or committing anything.

   If you already created and reported a branch (rule 1), you're done; the
   branch is left checked out for a human to inspect. If you got blocked
   *before* that — you couldn't determine which step to work on, or the
   `git checkout -b` itself failed — just print `HUMAN_REVIEW_REQUIRED`
   without a branch line. Don't invent an `AUTOPILOT_BRANCH=` value for a
   branch that doesn't exist; autopilot checks that the repo is actually on
   the branch you name and will stop anyway, with a more confusing message
   than the real reason you're reporting.

5. **Mark it done**: Once the step is fully implemented and tests pass, edit
   the plan file to mark that step's heading done — follow the exact
   convention already used elsewhere in the file (append `— **done**` to
   the heading; if nearby completed steps also add a short "Completed
   <date>." note, do the same, using today's UTC date via
   `date -u +%Y-%m-%d`). Only touch the heading/note for the step you just
   completed — don't edit any other step's text, status, or checklists.

6. **Write an implementation summary**: Add a subsection titled
   `Implementation Summary`, one heading level deeper than the step it
   belongs to (`####` under a `## Step N`, `#####` under a `### Na.`).

   Place it at the *end* of that step's own content — after the step's
   prose, and after its last sub-step if it has any, but before the next
   step's heading. Putting it immediately under a parent `## Step N` that
   has `### Na.` sub-steps would wedge it above those sub-steps and read as
   if it summarized the whole step before any of them ran.

   In it, briefly document:
   - what was actually changed (files/behavior), especially anywhere the
     implementation diverged from a literal reading of the step
   - any non-obvious decisions you made and why (the kind of judgment call
     rule 1's "make reasonable, conservative decisions" covers)
   - any issues you found *and fixed* along the way, even if unrelated to
     the step's literal text
   Keep it tight — a few sentences or a short bullet list, not an essay.

   If you found an issue that's out of scope for this step (fixing it would
   mean expanding beyond what the step describes, violating rule 1) and so
   you left it unresolved, don't silently drop it: append an entry for it to
   an `## Unresolved Issues` section at the very end of the plan file.
   Create that section (as the last section in the file) if it doesn't
   exist yet. Each entry should be a short bullet naming which step surfaced
   it, the date, and what the issue is — enough for a future step or a human
   to pick up. Never delete or resolve other steps' entries in this section
   yourself; only add to it.

7. **Narrate plainly**: briefly say which step you picked and why (if it
   wasn't simply the next one in document order), what you're about to do,
   and summarize what you did at the end — files touched, any assumptions
   you made, and anything worth a second look by a human reviewer. This
   narration is what ends up in the log (the log strips out extended
   thinking and tool diffs, keeping only your plain-text output), so it's
   the primary way your work gets reviewed. A step that finishes silently is
   a step nobody can audit later.
