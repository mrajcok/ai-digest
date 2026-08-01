#!/usr/bin/env bash
#
# run_tests.sh — the verification gate autopilot.sh runs before it commits
# and merges a step. Exits non-zero if anything fails, which makes autopilot
# stop for review and leave the step's changes uncommitted.
#
# CLAUDE.md requires `make lint` to be clean before committing, so lint is a
# gate here, not just tests.

set -uo pipefail

fail=0

echo "== make test =="
if ! make test; then
  echo "run_tests.sh: tests FAILED"
  fail=1
fi

echo "== make lint =="
if ! make lint; then
  echo "run_tests.sh: lint FAILED"
  fail=1
fi

if (( fail )); then
  echo "run_tests.sh: FAILED"
  exit 1
fi

echo "run_tests.sh: all checks passed"
