---
name: plan-step
description: Implements the next unfinished step in this project's plan file, marking it done when complete. Invoked by the overnight autopilot script — not meant to trigger automatically during normal interactive work.
disable-model-invocation: true
---

`$ARGUMENTS` is the path to this project's plan file.

You are running unattended overnight. Nobody can answer questions, so make
reasonable, conservative decisions and keep going — unless a rule below tells
you to stop.

## Workflow

Do these in order.

**1. Pick the step.** Read the plan file. Steps are headings numbered
`## Step N` or `### Na.`; a finished one ends in `— **done**`. Nothing else in
the file is a step. Take the first step that isn't done — but read the
surrounding prose first: if it gives an ordering or dependency reason ("2e
moved first because..."), follow that over document order.

If every step is done, print `NO_PENDING_STEPS`, change nothing, and stop.

**2. Branch.** Before editing anything:

```bash
git checkout -b "autopilot/$(date -u +%Y%m%d-%H%M%S)-<short-step-slug>"
```

Run `date` for real; don't write a timestamp from memory. The name must start
with `autopilot/`.

**3. Report the branch.** Confirm the checkout worked
(`git branch --show-current`), then print `AUTOPILOT_BRANCH=<branch-name>`. If
it failed, stop per Rule 5 — without printing a branch line.

**4. Implement exactly that one step.** Not the next one, not a neighboring
sub-step, and nothing beyond what the step's text describes.

**5. Verify.** Run the project's checks and make them pass. `./run_tests.sh`
is best — it runs `make test`, `make lint` (which must be clean) and
`shellcheck`, and is the same gate applied after this session ends.

**6. Mark it done.** Append `— **done**` to that step's heading, matching how
other finished headings in the file are written — including a `Completed
<date>.` note if they carry one, dated from `date -u +%Y-%m-%d`. Touch no
other step's text or status.

**7. Write the summary.** Add an `Implementation Summary` subsection one
heading level deeper than the step (`###` under `## Step N`, `####` under
`### Na.`), at the very end of that step's content — after its last sub-step,
before the next step's heading. A few sentences or short bullets:

- what changed (files, behavior), especially where you diverged from a
  literal reading of the step
- non-obvious decisions, and why
- issues you found and fixed along the way

Anything you found but left alone because fixing it would exceed this step's
scope goes in the plan's `## Open items` section instead, as a dated bullet
naming the step that surfaced it. Only add to that section; never edit or
resolve entries already in it.

## Rules

1. **One step per session**, even if a neighboring one looks related.

2. **No git beyond the one `git checkout -b`.** No `add`, `commit`, `merge`,
   `push`, `stash`, `reset`; no switching branches again. Staging, committing,
   merging and cleanup all happen after this session ends.

3. **Nothing leaves this machine, nothing costs money.** Never push a branch,
   never publish to `gh-pages`, never fire the Discord webhook at a live
   target, never run a pipeline stage that calls a paid LLM. Offline tests and
   read-only fetches (probing a feed you're writing a scraper for) are fine.

4. **Never edit `autopilot.sh` or `.claude/skills/plan-step/SKILL.md`.** If a
   step calls for it, stop per Rule 5.

5. **Stop when genuinely blocked** — the step is ambiguous in a way that
   changes the outcome, conflicts with existing code, or depends on something
   missing (a credential, a library decision, a prior step that wasn't
   actually done). Explain what's blocking you and what you'd need, print
   `HUMAN_REVIEW_REQUIRED`, and stop.

   Leave the changes you've already made in place; don't revert them. Don't
   mark the step done and don't write an Implementation Summary, since it
   didn't succeed — but a bullet in `## Open items` describing the blocker is
   welcome.

6. **Narrate plainly.** Say which step you picked (and why, if it wasn't
   simply the next one), what you're doing, and what you did — files touched,
   assumptions made, anything a reviewer should look at twice. Extended
   thinking and tool output are stripped from the log; this narration is the
   only record of your work.

## Sentinels

| Line | Meaning |
|---|---|
| `NO_PENDING_STEPS` | every step in the file is already done |
| `AUTOPILOT_BRANCH=<name>` | the branch you created for this step |
| `HUMAN_REVIEW_REQUIRED` | blocked; stopping |

Each is matched as a whole line. Print one alone on its own line, exactly, with
no prose, quotes, or backticks around it — and only when you mean it.
Mentioning one inside a sentence is safe.
