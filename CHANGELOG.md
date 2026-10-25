# Changelog

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
