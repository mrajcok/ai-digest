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
   for this step: `git checkout -b <branch-name>`. Name it something
   descriptive and collision-proof, e.g.
   `autopilot/$(date -u +%Y%m%d-%H%M%S)-<short-slug-of-the-step>`  — the
   timestamp matters because a blocked/abandoned branch from an earlier run
   is left in place (rule 4) and must never collide with a new attempt.
   Then print exactly one line: `AUTOPILOT_BRANCH=<branch-name>` (the exact
   name you just created, nothing else on the line). This is the only way
   autopilot learns which branch to merge afterward — if it's missing, the
   whole run stops for human review, so print it even if you end up blocked
   (rule 4) partway through.

   Implement exactly what the chosen step describes. Don't start on any
   other step, and don't expand or reinterpret its scope beyond what it
   says. Do one step (or one sub-step) per session, even if a neighboring
   one looks related — each session, and its branch, covers exactly one.

2. **Tests**: If the project has a test suite, run it before you finish and
   make sure it passes. Fix failures your changes introduced.

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
   the run without testing, merging, or committing anything. Make sure
   you've already printed `AUTOPILOT_BRANCH=<name>` (rule 1) even in this
   case, so a human knows which branch to go look at.

5. **Mark it done**: Once the step is fully implemented and tests pass, edit
   the plan file to mark that step's heading done — follow the exact
   convention already used elsewhere in the file (append `— **done**` to
   the heading; if nearby completed steps also add a short "Completed
   <date>." note, do the same, using today's UTC date via
   `date -u +%Y-%m-%d`). Only touch the heading/note for the step you just
   completed — don't edit any other step's text, status, or checklists.

6. **Write an implementation summary**: Immediately after the heading/note
   from rule 5 (and anything else already there), before the next heading,
   add a subsection titled `Implementation Summary` (one heading level
   deeper than the step, e.g. `####` under a `## Step N` or `#####` under a
   `### Na.`). In it, briefly document:
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
