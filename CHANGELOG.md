# Changelog

## v2.1.0 — 2026-10-26

- Better error reporting: a failed run now reports the actual error
  excerpt, not just the failing test name. The JSON report gains
  `failure_detail` (top level, plus `detail` per attempt) holding the
  FAILED line and the pytest `E` assertion/traceback lines, and the CLI
  prints it after `Failing:`. Failures with no `E` lines (collection or
  import errors, timeouts) fall back to the last output lines, so a
  failure never reports as a bare summary line. No change to fix
  behaviour, pass criteria, or the CLI.

## v2.0.0 — 2026-10-26

- Full-suite-only pass criterion, enforced in code (compile never counts).
- Timeout per pytest run, so hanging tests cannot hang the loop.
- Snapshot + restore: on final failure the original code is put back.
  No downgrade, ever.
- Pluggable fixer via `--fixer-script`, judged by file-change + full pass.
- JSON report (`--report`) with attempts and the exact failing test.
- Tests for the loop itself (`tests/test_stabilize.py`, 4 passing).
- Demo apps fixed: `demo-app` is clean and passing; `demo-app-buggy`
  carries a real double-tax bug the loop actually fixes.
- CLI unchanged from v1: `python3 stabilize.py demo-app` still works.

## v1 — 2026-10-26 (original)

- Hardcoded single-bug fixer, 5 attempts, no timeout, no restore,
  no report. Preserved in spirit; its QA judge prompt is unchanged
  in `QA_JUDGE_PROMPT.md`.
