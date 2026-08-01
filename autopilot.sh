#!/usr/bin/env bash
#
# autopilot.sh — run a project's plan file through Claude Code, one step at
# a time. See -h/--help for details.

set -uo pipefail

usage() {
  cat <<'EOF'
Usage: ./autopilot.sh [-h|--help] [plan_file]

Run a project's plan file through Claude Code one step at a time, each in a
fresh context, committing after each successful step.

plan_file, if given, overrides the PLAN_FILE environment variable and the
default location.

Run this from your project's root directory (a git repo). Assumes:
  - a single plan file (default docs/plan.md) documents every step, with
    each finished step/sub-step heading marked "— **done**"
  - an optional ./run_tests.sh is run before each commit
  - .claude/skills/plan-step/SKILL.md exists in this project

Each fresh Claude session reads the plan file itself and decides which
undone step to work on next — this script doesn't track that. It always
runs Claude with --dangerously-skip-permissions, since it's meant to run
unattended overnight with no one available to approve tool calls.

Each step is done on its own branch, created by the Claude session itself
(it reports the name back via an AUTOPILOT_BRANCH=<name> line — if that's
missing, the run stops for human review). On success this script commits on
that branch, merges it into whichever branch was checked out when the run
started, and deletes it. On a blocked step or a test failure, the branch is
left in place, uncommitted, for you to inspect.

Logs: each step's filtered Claude output plus this script's own messages
(prefixed "AP:") go to logs/autopilot-step-<n>.log, and the raw JSON stream
(needed for usage-limit detection, see below) goes to
logs/autopilot-step-<n>.raw.jsonl. <n> counts steps already marked done in
the plan file, so it keeps incrementing correctly across separate runs (a
blocked/retried step reuses and overwrites its number until it actually
succeeds).

Usage limits: if a step fails before creating its branch, this script
checks the raw output for a rate_limit_event with status "rejected" — an
undocumented but observed part of the stream-json output that includes the
exact resetsAt time — and sleeps until then before retrying. If that event
isn't found but the output still looks limit-related (plain text mention of
a usage/rate limit), it falls back to a blind LIMIT_RETRY_WAIT_SECONDS
sleep instead. Either way it retries up to MAX_LIMIT_RETRIES times before
giving up and stopping the run. If the limit is hit *after* a branch was
already created, this script does not guess: it stops for review rather
than risk abandoning partial work.

Run it inside tmux so it survives your SSH session ending:
  tmux new -s autopilot
  ./autopilot.sh
  [Ctrl-b then d to detach; `tmux attach -t autopilot` to check on it]

Environment variables:
  PLAN_FILE             Path to the plan file (default: docs/plan.md).
                        Prompted for interactively if it doesn't exist.
                        Overridden by plan_file if given on the command
                        line.
  MAX_STEPS             Safety cap on steps run per claude session
                        (default: 30).
  MAX_LIMIT_RETRIES     Consecutive suspected usage-limit hits to tolerate
                        on one step before giving up (default: 12 — at the
                        default wait below, that's 6h of retrying, enough
                        to outlast one 5h window with room to spare).
  LIMIT_RETRY_WAIT_SECONDS
                        Fallback sleep before retrying when a usage-limit
                        hit is suspected but no exact resetsAt time was
                        found in the output (default: 1800, i.e. 30m).
                        When resetsAt *is* found, this is ignored — the
                        script sleeps until that exact time instead.
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
  esac
done

PLAN_FILE="${1:-${PLAN_FILE:-docs/plan.md}}"
LOG_DIR="logs"
MAX_STEPS="${MAX_STEPS:-30}"   # safety cap so a bad run can't run forever
MAX_LIMIT_RETRIES="${MAX_LIMIT_RETRIES:-12}"
LIMIT_RETRY_WAIT_SECONDS="${LIMIT_RETRY_WAIT_SECONDS:-1800}"   # 30m

# top-level messages (not tied to a specific step) just print to the terminal
say() { echo "AP: $(date '+%Y-%m-%d %H:%M:%S') | $*"; }

if [[ -z "${TMUX:-}" ]]; then
  say "WARNING: not running inside tmux. If this shell dies (e.g. your SSH session drops), the run stops with it."
  say "Consider: tmux new -s autopilot   — then re-run this script inside that session."
fi

if ! command -v jq >/dev/null 2>&1; then
  say "ERROR: jq is required (used to filter Claude's output down to plain text, dropping thinking/tool detail)."
  say "Install it, e.g. 'apt install jq' or 'brew install jq', then re-run."
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "ERROR: not inside a git repository. Run this from your project root."
  exit 1
fi

BASE_BRANCH="$(git branch --show-current)"
if [[ -z "$BASE_BRANCH" ]]; then
  say "ERROR: not currently on a branch (detached HEAD?). Check out the branch you want steps merged into and re-run."
  exit 1
fi

if [[ ! -f "$PLAN_FILE" ]]; then
  say "Plan file '$PLAN_FILE' not found."
  read -r -p "AP: Enter the plan file's path, relative to the project root: " PLAN_FILE
  if [[ ! -f "$PLAN_FILE" ]]; then
    say "ERROR: '$PLAN_FILE' does not exist. Aborting."
    exit 1
  fi
fi

mkdir -p "$LOG_DIR"

say "=== Autopilot run starting on '$PLAN_FILE' (max $MAX_STEPS steps this run) ==="

steps_run=0
retry_pending=0
limit_hits=0

while (( steps_run < MAX_STEPS )); do
  current_branch="$(git branch --show-current)"
  if [[ "$current_branch" != "$BASE_BRANCH" ]]; then
    say "ERROR: expected to be on '$BASE_BRANCH' at the start of a step but am on '$current_branch'. Stopping."
    break
  fi

  step_num=$(( $(grep -cE '^#+ .*\*\*done\*\*' "$PLAN_FILE") + 1 ))
  step_log="$LOG_DIR/autopilot-step-${step_num}.log"
  raw_log="$LOG_DIR/autopilot-step-${step_num}.raw.jsonl"
  is_retry=$retry_pending
  retry_pending=0
  if (( ! is_retry )); then
    : > "$step_log"
    : > "$raw_log"
  fi

  # writes an AP-prefixed line to this step's log AND the terminal
  ap() { echo "AP: $(date '+%H:%M:%S') $*" | tee -a "$step_log"; }

  if (( is_retry )); then
    ap "Retrying step $step_num after a suspected usage-limit wait (attempt $((limit_hits + 1))/$MAX_LIMIT_RETRIES)"
  else
    ap "Starting step $step_num (session will pick the next undone step from $PLAN_FILE and branch off $BASE_BRANCH)"
  fi
  say "Starting step $step_num (log: $step_log)"

  # The skill reads the plan file path via \$ARGUMENTS and figures out
  # which undone step to work on. Claude's stderr and the raw stdout event
  # stream both go to raw_log — the filtered text-only stream_event deltas
  # (via jq) aren't enough to detect a usage-limit error, which may show up
  # as a plain stderr message or a non-text-delta JSON event.
  claude -p "/plan-step ${PLAN_FILE}" \
    --dangerously-skip-permissions \
    --output-format stream-json \
    --verbose \
    --include-partial-messages \
    2>>"$raw_log" \
    | tee -a "$raw_log" \
    | jq -rj 'select(.type == "stream_event" and .event.delta.type? == "text_delta") | .event.delta.text' \
    >> "$step_log"
  claude_exit=${PIPESTATUS[0]}
  echo >> "$step_log"   # ensure the next AP: line starts on its own line

  # Usage/rate-limit detection. Claude emits a structured
  # {"type":"rate_limit_event","rate_limit_info":{"status":...,"resetsAt":
  # <unix epoch>,...}} line on every request in the stream-json output —
  # confirmed by inspecting actual output, not documented, so treat as
  # liable to change. status "rejected" means that request was hard-blocked
  # by the limit; resetsAt tells us exactly when to retry. If no such event
  # is found, fall back to a blind text-pattern guess with a fixed wait.
  # Either way, only retried when no branch was created yet, so we never
  # risk silently abandoning partial work from a step that got further
  # before failing.
  usage_limit_hit=0
  limit_wait_seconds=""
  limit_wait_desc=""
  if [[ $claude_exit -ne 0 ]] && [[ "$(git branch --show-current)" == "$BASE_BRANCH" ]]; then
    rl_event=$(grep '"type":"rate_limit_event"' "$raw_log" | tail -n1)
    rl_status=$(jq -r '.rate_limit_info.status // empty' <<<"$rl_event" 2>/dev/null)
    rl_resets_at=$(jq -r '.rate_limit_info.resetsAt // empty' <<<"$rl_event" 2>/dev/null)

    if [[ "$rl_status" == "rejected" && -n "$rl_resets_at" ]]; then
      usage_limit_hit=1
      limit_wait_seconds=$(( rl_resets_at - $(date +%s) + 15 ))
      (( limit_wait_seconds < 60 )) && limit_wait_seconds=60
      limit_wait_desc="until the reported reset time ($(date -d "@$rl_resets_at" '+%Y-%m-%d %H:%M:%S %Z'))"
    elif grep -qiE 'usage limit|session limit|rate limit|quota exceeded|resets at|"error":"(rate_limit|billing_error)"' "$raw_log"; then
      usage_limit_hit=1
      limit_wait_seconds=$LIMIT_RETRY_WAIT_SECONDS
      limit_wait_desc="a blind ${LIMIT_RETRY_WAIT_SECONDS}s wait (no exact reset time reported)"
    fi
  fi

  if (( usage_limit_hit )); then
    limit_hits=$((limit_hits + 1))
    if (( limit_hits > MAX_LIMIT_RETRIES )); then
      ap "Hit what looks like a usage limit $limit_hits times in a row on step $step_num. Giving up — stopping for review."
      break
    fi
    ap "Looks like Claude hit a usage/rate limit before starting work on step $step_num (attempt $limit_hits/$MAX_LIMIT_RETRIES). Sleeping ${limit_wait_seconds}s ($limit_wait_desc) before retrying."
    say "Suspected usage limit; sleeping ${limit_wait_seconds}s before retrying step $step_num."
    sleep "$limit_wait_seconds"
    retry_pending=1
    continue
  fi
  limit_hits=0

  if [[ $claude_exit -ne 0 ]]; then
    ap "Claude exited with code $claude_exit on step $step_num. Stopping for review."
    break
  fi

  if grep -q "NO_PENDING_STEPS" "$step_log"; then
    ap "No pending steps remain in $PLAN_FILE. Done for the night."
    rm -f "$step_log" "$raw_log"
    break
  fi

  # The skill creates and checks out its own branch for the step and
  # reports its name this way — this is the only way we learn it.
  branch_name=$(sed -n 's/^AUTOPILOT_BRANCH=//p' "$step_log" | tail -n1)
  if [[ -z "$branch_name" ]]; then
    ap "Claude didn't report a branch name (AUTOPILOT_BRANCH=<name>) for step $step_num. Stopping for review."
    break
  fi

  if grep -q "HUMAN_REVIEW_REQUIRED" "$step_log"; then
    ap "Claude flagged step $step_num as blocked and needs human review. Leaving branch '$branch_name' checked out with any changes uncommitted."
    break
  fi

  if [[ -z "$(git status --porcelain)" ]]; then
    ap "No file changes were produced for step $step_num on branch '$branch_name'. Stopping for review — this usually means something went wrong."
    break
  fi

  if [[ -x "./run_tests.sh" ]]; then
    ap "Running tests..."
    if ! ./run_tests.sh >> "$step_log" 2>&1; then
      ap "Tests failed after step $step_num on branch '$branch_name'. Stopping for review — leaving changes uncommitted."
      break
    fi
  fi

  # Pull the step's name from the plan file diff itself: the line the skill
  # just marked "— **done**" is the best label we have for logs/commits.
  step_name=$(git diff -- "$PLAN_FILE" | grep -E '^\+#+ .*\*\*done\*\*' | head -n1 | sed -E 's/^\+#+ //')
  step_name="${step_name:-step $step_num}"

  git add -A
  git commit -m "Autopilot: ${step_name}" >> "$step_log" 2>&1

  ap "Merging '$branch_name' into $BASE_BRANCH..."
  if ! git checkout "$BASE_BRANCH" >> "$step_log" 2>&1 \
      || ! git merge --no-ff "$branch_name" -m "Merge $branch_name: ${step_name}" >> "$step_log" 2>&1; then
    ap "Merging '$branch_name' into $BASE_BRANCH failed. Stopping for review — resolve manually (currently on '$(git branch --show-current)')."
    break
  fi
  git branch -d "$branch_name" >> "$step_log" 2>&1

  steps_run=$((steps_run + 1))
  ap "Finished step $step_num: $step_name (merged '$branch_name' into $BASE_BRANCH)"
  say "Finished step $step_num: $step_name"
done

say "=== Autopilot run finished. $steps_run step(s) completed this run. ==="
