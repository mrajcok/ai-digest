#!/usr/bin/env bash
#
# run_tests.sh — the verification gate that should be run before a commit
# and merge step. Exits non-zero if anything fails.
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

echo "== shellcheck =="
if command -v shellcheck >/dev/null 2>&1; then
  # --cached --others also picks up a script a step just created but that
  # isn't committed yet — exactly when it most needs checking.
  mapfile -t sh_files < <(git ls-files --cached --others --exclude-standard '*.sh')
  if (( ${#sh_files[@]} )) && ! shellcheck "${sh_files[@]}"; then
    echo "run_tests.sh: shellcheck FAILED"
    fail=1
  fi
else
  echo "run_tests.sh: shellcheck not installed, skipping (apt install shellcheck)"
fi

if (( fail )); then
  echo "run_tests.sh: FAILED"
  exit 1
fi

echo "run_tests.sh: all checks passed"
