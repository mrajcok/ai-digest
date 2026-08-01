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
  - a clean working tree — anything uncommitted when the run starts would be
    swept into the first step's commit by `git add -A`, so the run refuses
    to start
  - a single plan file (default docs/plan.md) documents every step, with
    each finished step/sub-step heading marked "— **done**"
  - a ./run_tests.sh that exits non-zero if the project is broken; it is
    run before each commit. If it is missing, the run still proceeds but
    warns loudly — nothing is verifying the work.
  - .claude/skills/plan-step/SKILL.md exists in this project

Each fresh Claude session reads the plan file itself and decides which
undone step to work on next — this script doesn't track that. Sessions run
on AUTOPILOT_MODEL at AUTOPILOT_EFFORT (sonnet / high by default), always
with --dangerously-skip-permissions, since it's meant to run unattended
overnight with no one available to approve tool calls.

Each step is done on its own branch, created by the Claude session itself
(it reports the name back via an AUTOPILOT_BRANCH=<name> line — if that's
missing, the run stops for human review). The name must be under autopilot/,
because that prefix is the only thing that lets the *next* run recognize an
abandoned step branch. On success this script commits on that branch, merges
it into whichever branch was checked out when the run started, and deletes
it. Nothing is ever pushed by this script; you review and push in the
morning. On a blocked step or a test failure, the branch is left checked
out, uncommitted, for you to inspect — so the next run refuses to start
from an autopilot/* branch rather than treating it as the base.

A step is never committed if it modified autopilot.sh or the plan-step skill.
The skill forbids that, but `git add -A` would commit it regardless, and bash
reads this script incrementally from disk — an in-place rewrite can make the
*running* interpreter execute garbage mid-night.

A step is only committed if the session also marked its heading "— **done**"
in the plan file. Without that check a session that finished the work but
forgot the marker would be committed and merged, and then the next session
would read the same unmarked plan and redo the same step — repeatedly, all
night.

Logs: each step's filtered Claude output plus this script's own messages
(prefixed "AP:") go to logs/autopilot-step-<n>.log; the JSON stream goes to
logs/autopilot-step-<n>.raw.jsonl and Claude's stderr to
logs/autopilot-step-<n>.err. <n> is the next unused number, so no earlier
log is ever overwritten. The logs directory should be gitignored; the run
warns if it isn't.

The archived .raw.jsonl has the per-token "stream_event" records stripped
out — they are ~99% of the bytes and their text is already in the .log. The
*unfiltered* stream is kept only for the duration of one attempt, in a temp
file, which is what usage-limit detection reads. Without this an overnight
run of 30 steps leaves multiple GB in a directory you deliberately aren't
watching.

Usage limits: if a step ends without creating a branch and without reporting
a verdict, this script checks the raw output for a rate_limit_event with
status "rejected" — an undocumented but observed part of the stream-json
output that includes the exact resetsAt time — and sleeps until then before
retrying. That wait is capped at MAX_LIMIT_SLEEP_SECONDS, and is refused
outright if it would run past the MAX_RUN_SECONDS budget. A resetsAt more
than LIMIT_SANITY_MAX_SECONDS out (or well in the past) is not trusted at
all — that field silently switching to milliseconds would otherwise turn
into a capped 6h sleep that looks deliberate — so it falls back to a blind
wait instead. It also falls back to a blind LIMIT_RETRY_WAIT_SECONDS sleep
when no event is found but the session's stderr or its final result message
still looks limit-related. Either way it retries up to MAX_LIMIT_RETRIES
times before giving up.

A limit hit *after* the session branched is the common case, since the skill
branches before doing any work. If that branch is empty — clean tree, still
pointing at the base commit — there is nothing to lose, so the branch is
deleted and the step retried like any other limit hit. Otherwise this script
does not guess: it stops for review rather than risk abandoning partial work.

Run it inside tmux so it survives your SSH session ending:
  tmux new -s autopilot
  ./autopilot.sh
  [Ctrl-b then d to detach; `tmux attach -t autopilot` to check on it]

Environment variables:
  PLAN_FILE             Path to the plan file (default: docs/plan.md).
                        Prompted for interactively if it doesn't exist.
                        Overridden by plan_file if given on the command
                        line.
  AUTOPILOT_MODEL       Model each step's session runs on (default: sonnet).
                        An alias ('sonnet', 'opus', 'fable') or a full name
                        like 'claude-sonnet-5'. Passed straight to
                        `claude --model`.
  AUTOPILOT_EFFORT      Reasoning effort for each session — low, medium,
                        high, xhigh or max (default: high). Passed straight
                        to `claude --effort`. Both are checked at startup,
                        so a typo fails immediately rather than at 3am.
  MAX_STEPS             Safety cap on steps completed per run of this
                        script (default: 30).
  MAX_RUN_SECONDS       Wall-clock budget for the whole run (default:
                        43200, i.e. 12h). No new step is started once it is
                        spent, and no usage-limit sleep is allowed to run
                        past it. Set to 0 to disable.
  STEP_TIMEOUT_SECONDS  Kill a single Claude session that runs this long
                        (default: 3600, i.e. 1h) and stop for review. A
                        wedged session would otherwise park the run until
                        morning.
  TEST_TIMEOUT_SECONDS  Same, for one ./run_tests.sh invocation (default:
                        1800, i.e. 30m).
  MAX_LIMIT_RETRIES     Consecutive suspected usage-limit hits to tolerate
                        on one step before giving up (default: 12).
  LIMIT_RETRY_WAIT_SECONDS
                        Fallback sleep before retrying when a usage-limit
                        hit is suspected but no exact resetsAt time was
                        found in the output (default: 1800, i.e. 30m).
                        When resetsAt *is* found, the script sleeps until
                        that time instead.
  MAX_LIMIT_SLEEP_SECONDS
                        Upper bound on any single usage-limit sleep
                        (default: 21600, i.e. 6h). MAX_RUN_SECONDS is what
                        bounds them in aggregate.
  LIMIT_SANITY_MAX_SECONDS
                        How far in the future a reported resetsAt may be and
                        still be believed (default: 86400, i.e. 24h). Beyond
                        that — or more than 5 minutes in the past — the value
                        is treated as unparseable and the blind wait is used.
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
SKILL_FILE=".claude/skills/plan-step/SKILL.md"
# This script itself, as git sees it. Paired with SKILL_FILE as the two paths
# a step is never allowed to have modified — see the harness guard below.
SELF_FILE="autopilot.sh"
# Kept outside the repo: a lock file inside it would be swept up by the
# `git add -A` below and committed.
LOCK_FILE="${TMPDIR:-/tmp}/autopilot-$(printf '%s' "$PWD" | cksum | cut -d' ' -f1).lock"
AUTOPILOT_MODEL="${AUTOPILOT_MODEL:-sonnet}"
AUTOPILOT_EFFORT="${AUTOPILOT_EFFORT:-high}"
MAX_STEPS="${MAX_STEPS:-30}"   # safety cap so a bad run can't run forever
MAX_RUN_SECONDS="${MAX_RUN_SECONDS:-43200}"                     # 12h
STEP_TIMEOUT_SECONDS="${STEP_TIMEOUT_SECONDS:-3600}"            # 1h
TEST_TIMEOUT_SECONDS="${TEST_TIMEOUT_SECONDS:-1800}"            # 30m
MAX_LIMIT_RETRIES="${MAX_LIMIT_RETRIES:-12}"
LIMIT_RETRY_WAIT_SECONDS="${LIMIT_RETRY_WAIT_SECONDS:-1800}"    # 30m
MAX_LIMIT_SLEEP_SECONDS="${MAX_LIMIT_SLEEP_SECONDS:-21600}"     # 6h
LIMIT_SANITY_MAX_SECONDS="${LIMIT_SANITY_MAX_SECONDS:-86400}"   # 24h
# How far a reported resetsAt may lag "now" before it reads as stale rather
# than as a real (already-expired) limit.
LIMIT_SANITY_PAST_SECONDS=300

# Only ever matched against Claude's stderr and its final result message —
# never against the model's own narration, which in a project like this one
# routinely discusses rate limiting and would otherwise self-trigger.
LIMIT_TEXT_RE='usage limit|rate limit|quota exceeded|too many requests|resets at'

# top-level messages (not tied to a specific step) just print to the terminal
say() { echo "AP: $(date '+%Y-%m-%d %H:%M:%S') | $*"; }

# GNU date wants -d @<epoch>, BSD/macOS date wants -r <epoch>.
fmt_epoch() {
  date -d "@$1" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null \
    || date -r "$1" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null \
    || echo "epoch $1"
}

if [[ -z "${TMUX:-}" ]]; then
  say "WARNING: not running inside tmux. If this shell dies (e.g. your SSH session drops), the run stops with it."
  say "Consider: tmux new -s autopilot   — then re-run this script inside that session."
fi

if ! command -v claude >/dev/null 2>&1; then
  say "ERROR: 'claude' is not on PATH. Install Claude Code, or start the shell that has it, then re-run."
  exit 1
fi

case "$AUTOPILOT_EFFORT" in
  low|medium|high|xhigh|max) ;;
  *)
    say "ERROR: AUTOPILOT_EFFORT must be one of low, medium, high, xhigh, max (got '$AUTOPILOT_EFFORT')."
    exit 1
    ;;
esac

# Checked once here rather than discovered when step 1 dies with "unknown
# option" three hours into the night.
claude_help="$(claude --help 2>&1)"
for flag in --model --effort; do
  if ! grep -q -- "$flag " <<<"$claude_help"; then
    say "ERROR: this Claude Code ($(claude --version 2>/dev/null)) has no '$flag' option. Upgrade it, or edit the invocation below."
    exit 1
  fi
done

if ! command -v jq >/dev/null 2>&1; then
  say "ERROR: jq is required (used to filter Claude's output down to plain text, dropping thinking/tool detail)."
  say "Install it, e.g. 'apt install jq' or 'brew install jq', then re-run."
  exit 1
fi

# A hung session or a hung test suite would otherwise silently consume the
# whole night, so both children are wrapped in timeout when it's available.
TIMEOUT_BIN=""
for candidate in timeout gtimeout; do
  if command -v "$candidate" >/dev/null 2>&1; then
    TIMEOUT_BIN="$candidate"
    break
  fi
done
if [[ -z "$TIMEOUT_BIN" ]]; then
  say "WARNING: no 'timeout' (or 'gtimeout') on PATH — a wedged Claude session or test run can park this script indefinitely."
  say "On macOS: brew install coreutils."
  step_timeout=()
  test_timeout=()
else
  step_timeout=("$TIMEOUT_BIN" -k 30 "$STEP_TIMEOUT_SECONDS")
  test_timeout=("$TIMEOUT_BIN" -k 30 "$TEST_TIMEOUT_SECONDS")
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  say "ERROR: not inside a git repository. Run this from your project root."
  exit 1
fi

if [[ ! -f "$SKILL_FILE" ]]; then
  say "ERROR: $SKILL_FILE not found — that skill is what actually implements each step."
  exit 1
fi

BASE_BRANCH="$(git branch --show-current)"
if [[ -z "$BASE_BRANCH" ]]; then
  say "ERROR: not currently on a branch (detached HEAD?). Check out the branch you want steps merged into and re-run."
  exit 1
fi

# A previous run that stopped for review leaves its step branch checked out.
# Starting from there would quietly make that abandoned branch the merge
# target for everything that follows.
if [[ "$BASE_BRANCH" == autopilot/* ]]; then
  say "ERROR: currently on '$BASE_BRANCH', which looks like an abandoned autopilot step branch."
  say "A previous run probably stopped for review here. Inspect it, then check out your real base branch (e.g. 'git checkout main') and re-run."
  exit 1
fi

# A merge this script failed to complete leaves conflict markers on the base
# branch — which is *not* an autopilot/* branch, so the guard above misses
# it. Starting a new run there would `git add -A` those markers into a step
# commit.
if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
  say "ERROR: a merge is in progress on '$BASE_BRANCH' — a previous run's merge probably conflicted."
  say "Resolve or abort it ('git merge --abort'), then re-run."
  exit 1
fi

# Every step is committed with `git add -A`, so anything already dirty would
# ride along into the first step's commit and get merged into the base branch.
if [[ -n "$(git status --porcelain)" ]]; then
  say "ERROR: the working tree isn't clean. Autopilot commits with 'git add -A', so uncommitted work would be swept into a step's commit."
  say "If you didn't leave these: a previous run may have stopped for review before its session got as far as branching, leaving the step's partial work here on '$BASE_BRANCH'. Check the newest logs/autopilot-step-*.log."
  say "Commit, stash, or discard your changes, then re-run. Current status:"
  git status --short
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

if [[ ! -x "./run_tests.sh" ]]; then
  say "WARNING: no executable ./run_tests.sh — nothing will verify a step before it is committed and merged."
fi

# Refuse to run two autopilots against the same working tree. A lock left by
# a killed run (tmux kill-session sends SIGHUP) is taken over rather than
# blocking the next night entirely.
if ! ( set -o noclobber; echo "$$" > "$LOCK_FILE" ) 2>/dev/null; then
  lock_pid="$(cat "$LOCK_FILE" 2>/dev/null)"
  if [[ "$lock_pid" =~ ^[0-9]+$ ]] && kill -0 "$lock_pid" 2>/dev/null; then
    say "ERROR: another autopilot (pid $lock_pid) is already running against this working tree."
    say "If that's wrong, remove the lock: rm $LOCK_FILE"
    exit 1
  fi
  say "WARNING: stale lock $LOCK_FILE (pid ${lock_pid:-unknown} is not running). Taking it over."
  echo "$$" > "$LOCK_FILE"
fi

SCRATCH_DIR="$(mktemp -d)"
cleanup() {
  rm -f "$LOCK_FILE"
  rm -rf "$SCRATCH_DIR"
}
# EXIT alone doesn't fire on an untrapped SIGTERM/SIGHUP, which is exactly how
# this script dies when a tmux session is killed. Caveat: bash defers a trap
# until the running foreground command returns, and `timeout` runs Claude in
# its own process group, so a signal aimed here may not be acted on until the
# current session ends or hits STEP_TIMEOUT_SECONDS. The stale-lock takeover
# above is the backstop for whatever slips through.
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM HUP

attempt_text="$SCRATCH_DIR/attempt.txt"
attempt_raw="$SCRATCH_DIR/attempt.jsonl"
attempt_err="$SCRATCH_DIR/attempt.err"
attempt_status="$SCRATCH_DIR/attempt.status"
done_before="$SCRATCH_DIR/done-before.txt"
done_after="$SCRATCH_DIR/done-after.txt"

# jq programs live in files so the pipeline can stay legible inside the
# `bash -c` wrapper below, which is already one level of quoting deep.
#
# Both read with -R and parse per line via `fromjson?`: jq exits (status 5)
# on the first malformed line otherwise, discarding everything after it —
# and a stream truncated mid-line is exactly what killing Claude produces.
# Losing the tail of the stream means losing the sentinels and the
# AUTOPILOT_BRANCH line, which this script then misreads as "did nothing".
text_filter="$SCRATCH_DIR/text-delta.jq"
cat > "$text_filter" <<'EOF'
fromjson?
| select(.type == "stream_event" and .event.delta.type? == "text_delta")
| .event.delta.text
EOF

# Passes every line through unchanged except the per-token stream_event
# records, whose text is already in the .log. Malformed lines are kept:
# they're the interesting ones when something went wrong.
archive_filter="$SCRATCH_DIR/archive.jq"
cat > "$archive_filter" <<'EOF'
if (try (fromjson | .type) catch "") == "stream_event" then empty else . end
EOF

mkdir -p "$LOG_DIR"

# Probed with a filename rather than the bare directory: check-ignore reports
# a directory as un-ignored when it holds a tracked file (logs/.gitkeep).
if ! git check-ignore -q "$LOG_DIR/autopilot-probe.log" 2>/dev/null; then
  say "WARNING: '$LOG_DIR/' is not gitignored — 'git add -A' will commit this run's logs along with each step."
  say "Add '$LOG_DIR/' to .gitignore."
fi

RUN_START=$(date +%s)
run_deadline=$(( RUN_START + MAX_RUN_SECONDS ))

say "=== Autopilot run starting on '$PLAN_FILE' (max $MAX_STEPS steps this run, base branch '$BASE_BRANCH') ==="
say "Model: $AUTOPILOT_MODEL, effort: $AUTOPILOT_EFFORT."
if (( MAX_RUN_SECONDS > 0 )); then
  say "Run budget: ${MAX_RUN_SECONDS}s — no new step will start after $(fmt_epoch "$run_deadline")."
fi

steps_run=0
retry_pending=0
limit_hits=0
step_log=""
raw_log=""
err_log=""

while (( steps_run < MAX_STEPS )); do
  if (( MAX_RUN_SECONDS > 0 )) && (( $(date +%s) >= run_deadline )); then
    say "Reached the ${MAX_RUN_SECONDS}s run budget. Not starting another step."
    break
  fi

  current_branch="$(git branch --show-current)"
  if [[ "$current_branch" != "$BASE_BRANCH" ]]; then
    say "ERROR: expected to be on '$BASE_BRANCH' at the start of a step but am on '$current_branch'. Stopping."
    break
  fi

  # Snapshot which headings are already marked done. The gate before the
  # commit compares against this rather than reading the diff: a diff shows a
  # '+' line for an existing done heading that was merely reworded, which
  # would pass a "did it mark something done?" check without any step
  # actually having been finished.
  grep -E '^#+ .*\*\*done\*\*' "$PLAN_FILE" | sort > "$done_before"
  done_before_count=$(grep -cE '^#+ .*\*\*done\*\*' "$PLAN_FILE")

  # On a fresh step, claim the next unused log number so no earlier log —
  # including a failed step's, which is the one you most want to keep — is
  # ever overwritten. A usage-limit retry keeps appending to its own logs.
  if (( ! retry_pending )); then
    step_num=$(( done_before_count + 1 ))
    while [[ -e "$LOG_DIR/autopilot-step-${step_num}.log" ]]; do
      step_num=$((step_num + 1))
    done
    step_log="$LOG_DIR/autopilot-step-${step_num}.log"
    raw_log="$LOG_DIR/autopilot-step-${step_num}.raw.jsonl"
    err_log="$LOG_DIR/autopilot-step-${step_num}.err"
    : > "$step_log"
    : > "$raw_log"
    : > "$err_log"
  fi
  is_retry=$retry_pending
  retry_pending=0

  # writes an AP-prefixed line to this step's log AND the terminal
  ap() { echo "AP: $(date '+%H:%M:%S') $*" | tee -a "$step_log"; }

  if (( is_retry )); then
    ap "Retrying step $step_num after a usage-limit wait ($limit_hits of $MAX_LIMIT_RETRIES tolerated hits so far)"
  else
    ap "Starting step $step_num (session will pick the next undone step from $PLAN_FILE and branch off $BASE_BRANCH)"
  fi
  say "Starting step $step_num (log: $step_log)"

  # Each attempt writes to its own scratch files — stdout, raw stream *and*
  # stderr — so the checks below only ever see *this* attempt's output. A
  # stale limit message from a previous attempt lingering in a cumulative
  # file would otherwise re-trigger the retry logic on every later attempt.
  # The scratch files are appended to the cumulative logs afterward for
  # auditing. stderr gets its own file rather than sharing stdout's: two
  # processes appending concurrently can interleave mid-line and corrupt the
  # JSON that usage-limit detection parses.
  : > "$attempt_text"
  : > "$attempt_raw"
  : > "$attempt_err"
  : > "$attempt_status"

  # The whole pipeline runs under one `timeout`, not just Claude. Wrapping
  # only Claude leaves the shell waiting on `jq`, which blocks until every
  # writer closes the pipe — and Claude's own Bash tool can leave a
  # backgrounded process holding that inherited fd. The run would then park
  # past STEP_TIMEOUT_SECONDS anyway, which is the one thing the timeout
  # exists to prevent. timeout signals the whole process group here, so the
  # readers die with the writer.
  #
  # Claude's exit status has to come back through a file: PIPESTATUS belongs
  # to the inner shell. The skill reads the plan file path via $ARGUMENTS and
  # figures out which undone step to work on.
  # shellcheck disable=SC2016  # $1..$8 are the inner shell's positional args,
  # passed after the script string below — expanding them here is exactly wrong.
  "${step_timeout[@]}" bash -c '
    set -uo pipefail
    claude -p "/plan-step \"$1\"" \
      --model "$2" \
      --effort "$3" \
      --dangerously-skip-permissions \
      --output-format stream-json \
      --verbose \
      --include-partial-messages \
      2>>"$5" \
      | tee -a "$6" \
      | jq -Rrj -f "$7" \
      >> "$4"
    echo "${PIPESTATUS[0]}" > "$8"
  ' autopilot-attempt \
      "$PLAN_FILE" "$AUTOPILOT_MODEL" "$AUTOPILOT_EFFORT" \
      "$attempt_text" "$attempt_err" "$attempt_raw" "$text_filter" "$attempt_status"
  wrapper_exit=$?

  # Empty status file = the wrapper never reached that line, i.e. it was
  # killed; its own exit code (124/137 from timeout) is the real story.
  claude_exit="$(tr -dc '0-9' < "$attempt_status" 2>/dev/null)"
  [[ -n "$claude_exit" ]] || claude_exit=$wrapper_exit

  echo >> "$attempt_text"   # ensure the next AP: line starts on its own line

  cat "$attempt_text" >> "$step_log"
  jq -Rr -f "$archive_filter" "$attempt_raw" >> "$raw_log" 2>/dev/null
  cat "$attempt_err"  >> "$err_log"

  # Sentinels are matched as whole lines: the model discusses them by name
  # ("I am not printing NO_PENDING_STEPS because ..."), so a substring match
  # would produce false positives. Surrounding whitespace is tolerated —
  # a single trailing space shouldn't cost a night's work.
  saw_no_pending=0
  saw_review=0
  grep -qE '^[[:space:]]*NO_PENDING_STEPS[[:space:]]*$'      "$attempt_text" && saw_no_pending=1
  grep -qE '^[[:space:]]*HUMAN_REVIEW_REQUIRED[[:space:]]*$' "$attempt_text" && saw_review=1
  branch_name=$(sed -n 's/^[[:space:]]*AUTOPILOT_BRANCH=//p' "$attempt_text" | tail -n1 | tr -d '[:space:]')

  # The skill branches before doing any work, so the most common way to be
  # rate-limited is *after* a branch exists — which used to disqualify the
  # step from the retry path below and end the night on an autopilot/* branch,
  # which in turn stopped the *next* night from starting. One limit at 1am
  # cost two runs.
  #
  # An empty branch is not partial work: clean tree, still pointing at the
  # base commit. Discarding it is lossless and puts the step back in exactly
  # the state the retry path expects.
  discarded_empty_branch=0
  if [[ -n "$branch_name" ]] && (( ! saw_no_pending )) && (( ! saw_review )) \
     && [[ "$(git branch --show-current)" == "$branch_name" ]] \
     && [[ -z "$(git status --porcelain)" ]] \
     && [[ "$(git rev-parse HEAD)" == "$(git rev-parse "$BASE_BRANCH")" ]]; then
    ap "Step $step_num branched to '$branch_name' but produced nothing. Discarding the empty branch and returning to $BASE_BRANCH."
    if git checkout "$BASE_BRANCH" >> "$step_log" 2>&1 \
       && git branch -D "$branch_name" >> "$step_log" 2>&1; then
      discarded_empty_branch=1
      branch_name=""
    else
      ap "WARNING: couldn't discard '$branch_name' (now on '$(git branch --show-current)'). Leaving it for review."
    fi
  fi

  # Usage/rate-limit detection. Claude emits a structured
  # {"type":"rate_limit_event","rate_limit_info":{"status":...,"resetsAt":
  # <unix epoch>,"rateLimitType":...}} line on every request in the
  # stream-json output — confirmed by inspecting actual output, not
  # documented, so treat as liable to change. status "rejected" means that
  # request was hard-blocked; resetsAt says when to retry.
  #
  # The precondition is that the session accomplished nothing: no branch (or
  # only the empty one just discarded above), no verdict sentinel, still on
  # the base branch. That means retrying is safe — there's no partial work to
  # abandon. Deliberately *not* gated on a non-zero exit code: `claude -p`
  # doesn't reliably exit non-zero when a request is refused.
  usage_limit_hit=0
  limit_wait_seconds=0
  limit_wait_desc=""
  if (( ! saw_no_pending )) && (( ! saw_review )) && [[ -z "$branch_name" ]] \
     && [[ "$(git branch --show-current)" == "$BASE_BRANCH" ]]; then
    # Parsed with jq rather than grepped: a whitespace change in the stream's
    # JSON formatting would silently disable a grep for '"type":"..."'. Read
    # per line via fromjson? for the same reason as the text filter — one
    # truncated line must not take the whole parse down with it.
    rl_info=$(jq -Rr 'fromjson?
                      | select(.type == "rate_limit_event")
                      | .rate_limit_info
                      | select(.status == "rejected")
                      | "\(.resetsAt // "") \(.rateLimitType // "unknown")"' \
                  "$attempt_raw" 2>/dev/null | tail -n1)
    rl_resets_at="${rl_info%% *}"
    rl_type="${rl_info##* }"
    saw_rejection=0
    [[ -n "$rl_info" ]] && saw_rejection=1

    # A resetsAt is only believed if it lands in a plausible window. The
    # failure this guards against is the field's units changing (epoch
    # milliseconds reads as a date ~55,000 years out): the cap below would
    # quietly turn that into a deliberate-looking 6h sleep. A timestamp in
    # the past is just as wrong, and clamping it to 60s would burn every
    # retry in twelve minutes against a limit that hasn't moved.
    rl_trusted=0
    if [[ "$rl_resets_at" =~ ^[0-9]+$ ]]; then
      rl_delta=$(( rl_resets_at - $(date +%s) ))
      if (( rl_delta >= -LIMIT_SANITY_PAST_SECONDS && rl_delta <= LIMIT_SANITY_MAX_SECONDS )); then
        rl_trusted=1
      else
        ap "Ignoring reported reset time $rl_resets_at (${rl_delta}s away) — outside the plausible window, so the field's meaning may have changed. Falling back to a blind wait."
      fi
    fi

    if (( rl_trusted )); then
      usage_limit_hit=1
      limit_wait_seconds=$(( rl_delta + 15 ))
      limit_wait_desc="until the reported ${rl_type:-unknown} reset time ($(fmt_epoch "$rl_resets_at"))"
      if (( limit_wait_seconds > MAX_LIMIT_SLEEP_SECONDS )); then
        limit_wait_desc="$limit_wait_desc, capped at ${MAX_LIMIT_SLEEP_SECONDS}s"
        limit_wait_seconds=$MAX_LIMIT_SLEEP_SECONDS
      fi
      (( limit_wait_seconds < 60 )) && limit_wait_seconds=60
    else
      # Fallback: a rejection we couldn't time, or — failing that — the CLI's
      # own stderr plus the text of the final result message. Both of those
      # are the harness talking, not the model.
      result_text=$(jq -Rr 'fromjson?
                            | select(.type == "result")
                            | [.result?, .error?]
                            | map(select(. != null) | tostring)
                            | join(" ")' "$attempt_raw" 2>/dev/null)
      if (( saw_rejection )) \
         || grep -qiE "$LIMIT_TEXT_RE" <<<"$result_text" \
         || grep -qiE "$LIMIT_TEXT_RE" "$attempt_err"; then
        usage_limit_hit=1
        limit_wait_seconds=$LIMIT_RETRY_WAIT_SECONDS
        limit_wait_desc="a blind ${LIMIT_RETRY_WAIT_SECONDS}s wait (no usable reset time reported)"
      fi
    fi
  fi

  if (( usage_limit_hit )); then
    limit_hits=$((limit_hits + 1))
    if (( limit_hits > MAX_LIMIT_RETRIES )); then
      ap "Hit a usage limit $limit_hits times in a row on step $step_num. Giving up — stopping for review."
      break
    fi
    if (( MAX_RUN_SECONDS > 0 )); then
      remaining=$(( run_deadline - $(date +%s) ))
      if (( limit_wait_seconds >= remaining )); then
        ap "Usage limit on step $step_num would need a ${limit_wait_seconds}s wait, but only ${remaining}s of the ${MAX_RUN_SECONDS}s run budget remain. Stopping."
        break
      fi
    fi
    ap "Claude hit a usage/rate limit before starting work on step $step_num (attempt $limit_hits/$MAX_LIMIT_RETRIES). Sleeping ${limit_wait_seconds}s ($limit_wait_desc) before retrying."
    say "Usage limit; sleeping ${limit_wait_seconds}s before retrying step $step_num."
    sleep "$limit_wait_seconds"
    retry_pending=1
    continue
  fi
  limit_hits=0

  # 124 is timeout's own exit code; 137 is a SIGKILL from its -k grace period.
  if (( claude_exit == 124 || claude_exit == 137 )); then
    ap "Claude exceeded the ${STEP_TIMEOUT_SECONDS}s step timeout on step $step_num and was killed. Stopping for review."
    break
  fi

  if [[ $claude_exit -ne 0 ]]; then
    ap "Claude exited with code $claude_exit on step $step_num. Stopping for review (stderr: $err_log)."
    break
  fi

  if (( saw_no_pending )); then
    ap "No pending steps remain in $PLAN_FILE. Done for the night."
    # Kept, not deleted: if that verdict is wrong (a misread heading, a
    # truncated file), this log is the only record of the reasoning. Renamed
    # so it isn't mistaken for a step that ran, and so the number stays free.
    for f in "$step_log" "$raw_log" "$err_log"; do
      [[ -e "$f" ]] && mv "$f" "$f.nopending"
    done
    break
  fi

  # Checked before the branch-name check: a session blocked before it could
  # pick a step has no branch to report, and the real blocker is the more
  # useful thing to surface. A blocked step always ends the run.
  if (( saw_review )); then
    if [[ -n "$branch_name" ]]; then
      ap "Claude flagged step $step_num as blocked and needs human review. Leaving branch '$branch_name' checked out with its changes uncommitted — which is also what stops the next run from starting here."
    elif [[ -n "$(git status --porcelain)" ]]; then
      # It never got as far as branching, so its partial work is sitting on
      # the base branch. Say so plainly: the next run's only complaint will
      # be "working tree isn't clean", which reads like the user's own mess.
      ap "Claude flagged step $step_num as blocked before it created a branch, and left changes uncommitted on '$BASE_BRANCH'. Stopping for review — the next run will refuse to start until this tree is clean."
      git status --short >> "$step_log"
    else
      ap "Claude flagged step $step_num as blocked before it created a branch, and changed nothing. Stopping for review — the repo is untouched on '$BASE_BRANCH'."
    fi
    break
  fi

  # The skill creates and checks out its own branch for the step and reports
  # its name this way — this is the only way we learn it.
  if [[ -z "$branch_name" ]]; then
    if (( discarded_empty_branch )); then
      ap "Step $step_num branched but produced nothing, and it doesn't look like a usage limit. Stopping for review (the empty branch was discarded; stderr: $err_log)."
    else
      ap "Claude didn't report a branch name (AUTOPILOT_BRANCH=<name>) for step $step_num. Stopping for review."
    fi
    break
  fi

  # The autopilot/ prefix isn't cosmetic: it is the only thing the *next*
  # run's abandoned-branch guard recognizes. A step branch named anything
  # else that gets left checked out would silently become the base branch
  # everything after it merges into.
  if [[ "$branch_name" != autopilot/* ]]; then
    ap "Claude reported branch '$branch_name', which isn't under 'autopilot/'. Stopping for review — a step branch outside that namespace defeats the next run's abandoned-branch check."
    break
  fi

  # If the skill's `git checkout -b` silently failed, we'd otherwise commit
  # the step's work straight onto the base branch.
  actual_branch="$(git branch --show-current)"
  if [[ "$actual_branch" != "$branch_name" ]]; then
    ap "Claude reported branch '$branch_name' but the repo is on '$actual_branch'. Stopping for review — refusing to commit to the wrong branch."
    break
  fi

  if [[ -z "$(git status --porcelain)" ]]; then
    ap "No file changes were produced for step $step_num on branch '$branch_name'. Stopping for review — this usually means something went wrong."
    break
  fi

  # The skill forbids the session from editing its own harness, but nothing
  # stopped `git add -A` from committing it anyway — and bash reads this
  # script incrementally from disk, so an in-place rewrite can make the
  # *running* interpreter execute garbage from a shifted byte offset. That is
  # not a failure you can diagnose from a log.
  harness_edits="$(git status --porcelain -- "$SELF_FILE" "$SKILL_FILE")"
  if [[ -n "$harness_edits" ]]; then
    ap "Step $step_num modified its own harness. Stopping for review — refusing to commit changes to $SELF_FILE or $SKILL_FILE:"
    printf '%s\n' "$harness_edits" | tee -a "$step_log"
    break
  fi

  # `git add -A` sweeps in whatever else the session left lying around — a
  # throwaway verification harness, a stray fixture. Record the full set
  # that's about to be committed, and flag new files by name, since those are
  # the ones that slip through review unnoticed.
  git status --porcelain >> "$step_log"
  untracked="$(git ls-files --others --exclude-standard)"
  if [[ -n "$untracked" ]]; then
    ap "Step $step_num added $(printf '%s\n' "$untracked" | wc -l | tr -d ' ') new file(s), all of which 'git add -A' will commit:"
    printf '%s\n' "$untracked" | sed 's/^/    /' | tee -a "$step_log"
  fi

  if [[ -x "./run_tests.sh" ]]; then
    ap "Running tests..."
    "${test_timeout[@]}" ./run_tests.sh >> "$step_log" 2>&1
    test_exit=$?
    if (( test_exit == 124 || test_exit == 137 )); then
      ap "./run_tests.sh exceeded the ${TEST_TIMEOUT_SECONDS}s timeout on step $step_num and was killed. Stopping for review — leaving changes uncommitted."
      break
    fi
    if (( test_exit != 0 )); then
      ap "Tests failed after step $step_num on branch '$branch_name'. Stopping for review — leaving changes uncommitted."
      break
    fi
  else
    ap "WARNING: no ./run_tests.sh — committing step $step_num unverified."
  fi

  # The plan file is the only record of which steps are finished, so a
  # session that did the work but didn't mark the heading would be redone,
  # identically, by every session after it.
  #
  # Compared against the snapshot taken at the top of this step, not against
  # the diff: a diff shows a '+' line for a done heading that was merely
  # reworded or re-indented, which would satisfy a "did it add a **done**
  # heading?" check while no step was actually finished. The count is what
  # makes this exact — rewording changes which lines are new but not how
  # many there are.
  grep -E '^#+ .*\*\*done\*\*' "$PLAN_FILE" | sort > "$done_after"
  done_after_count=$(grep -cE '^#+ .*\*\*done\*\*' "$PLAN_FILE")
  if (( done_after_count <= done_before_count )); then
    ap "Step $step_num didn't mark any new heading '**done**' in $PLAN_FILE ($done_before_count before, $done_after_count now). Stopping for review — committing this would let the next session redo the same step."
    ap "The work is on branch '$branch_name', uncommitted, and tests passed; mark the heading and merge by hand if it's good."
    break
  fi
  done_headings="$(comm -13 "$done_before" "$done_after")"

  # Name the commit after the heading the skill just marked done. Prefer the
  # most specific one: a session finishing sub-step 2h may also mark its
  # parent Step 2 done, and "2h" is the more accurate label for this commit.
  step_name=$(printf '%s\n' "$done_headings" \
    | awk 'NF { print gsub(/#/,"#"), $0 }' \
    | sort -rn \
    | head -n1 \
    | sed -E 's/^[0-9]+ #+ //' \
    | sed -E 's/[[:space:]]*(—|–|--|-)?[[:space:]]*\*\*done\*\*.*$//')
  step_name="${step_name:-step $step_num}"

  git add -A
  if ! git commit -m "Autopilot: ${step_name}" >> "$step_log" 2>&1; then
    ap "git commit failed for step $step_num on branch '$branch_name' (a hook may have rejected it). Stopping for review."
    break
  fi

  ap "Merging '$branch_name' into $BASE_BRANCH..."
  if ! git checkout "$BASE_BRANCH" >> "$step_log" 2>&1 \
      || ! git merge --no-ff "$branch_name" -m "Merge $branch_name: ${step_name}" >> "$step_log" 2>&1; then
    ap "Merging '$branch_name' into $BASE_BRANCH failed. Stopping for review — resolve manually (currently on '$(git branch --show-current)')."
    break
  fi
  if ! git branch -d "$branch_name" >> "$step_log" 2>&1; then
    # Harmless — the work is merged — but an unexplained leftover autopilot/*
    # branch in the morning looks exactly like a step that failed.
    ap "WARNING: couldn't delete '$branch_name' after merging it. The step is committed and merged; clean up with: git branch -D '$branch_name'"
  fi

  steps_run=$((steps_run + 1))
  ap "Finished step $step_num: $step_name (merged '$branch_name' into $BASE_BRANCH)"
  say "Finished step $step_num: $step_name"
done

say "=== Autopilot run finished. $steps_run step(s) completed this run. This script pushed nothing; review and push in the morning. ==="
