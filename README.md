# Autonomous Stabilize Loop

A free, open-source write-test-stabilize loop. No paid licenses, no vendor lock-in.

It runs your test suite, and if it fails, it tries to fix the code and
re-runs the **full** suite. It stops only when the full suite passes, or
when it cannot fix the failure — in which case it **restores your original
code** and reports the exact failing test. It never leaves your app worse
than it found it.

## The rule (from `QA_JUDGE_PROMPT.md`)

> Never declare success because code compiles. Success = the full test
> suite passes. If you cannot fix it within 5 attempts, stop and report
> the exact blocker — do not fake a pass.

That rule is enforced in `stabilize.py`, not just written in a prompt:
compile checks are a sanity check only and can never count as a pass.

## Quick start

```bash
python3 stabilize.py demo-app
python3 stabilize.py demo-app-buggy --report report.json
```

`demo-app` passes on attempt 1. `demo-app-buggy` has a real double-tax
bug, fails `test_total` on attempt 1, gets patched, and passes the full
suite on attempt 2.

## Options

```
python3 stabilize.py <app_dir> [--max-attempts 5] [--timeout 120]
                       [--fixer-script ./my_fixer.py] [--report report.json]
```

- `--fixer-script`: a script called as `script <app_dir> <error_log_file>`.
  It must exit 0 **and** change a file to count as a fix. The built-in
  fixer is only a demo for one known bug class (double-counted tax) —
  for real work, plug in your own fixer (an LLM call, a linter autofix,
  anything). The loop judges every fixer the same way: full suite pass,
  or it does not count.
- `--report`: JSON report with attempts, failing test, and whether the
  original code was restored — plus `failure_detail`, the actual error
  excerpt (FAILED line + pytest `E` assertion lines), so a failure can
  be diagnosed from the report alone.

## Safety

- Original code — every app file, directory, and symlink, not just
  Python files — is snapshotted before any fix. On final failure it
  is restored, and files, directories, and symlinks a fixer created
  are deleted. Symlinks are recorded by target, never followed.
  File and directory permission bits are snapshotted too, so a fixer
  that chmod'ed something cannot leave that change behind.
  Cache/VCS directories are excluded.
- Every attempt runs the full suite, never just the failing test.
- Each pytest run has a timeout, so a hanging test cannot hang the loop.
- Exit codes: `0` = stable, `1` = could not stabilize (code restored),
  `2` = usage error (missing dir, no tests found).

## Tests for the loop itself

```bash
python3 -m pytest -q tests/
```

9 tests: clean app passes first try, buggy app is fixed, an unfixable
app is restored unchanged with its failing test named, an unfixable
report carries the error detail, a missing app dir is a usage error,
a data-file-only fix counts and passes, a failed run restores
changed data files and deletes fixer-created files, a failed run
restores a deleted empty directory and removes a fixer-created
directory and symlink, and a failed run restores a chmod'ed file's
contents and its original permission bits.

## v1 → v2 changes

v1 (the original `stabilize.py`) had one hardcoded patch, no timeout,
no backup/restore, no report, and a demo app whose comment claimed a
bug the code did not have. v2 keeps v1's CLI (`stabilize.py demo-app`
still works) and fixes all of that. See `CHANGELOG.md`.
