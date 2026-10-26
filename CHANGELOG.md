# Changelog

## v2.7.0 — 2026-10-26

- Safety fix, no behaviour change for passing runs: the snapshot and
  restore now cover the app directory itself, not just the entries
  inside it. A fixer that chmod'ed the app directory (e.g. 0o750 to
  0o777) used to leave that change behind while the report still
  claimed the original code was restored — the last untrue-claim
  hole in the chain v2.2–v2.6 closed for contents, shape, modes,
  and special files. The root mode is recorded under "." and flows
  through the same machinery as any other directory: a root-only
  chmod counts as a fixer change, the restore makes the root
  owner-writable first (so a fixer that made it read-only cannot
  block the content restore), the exact original mode is put back
  in the final pass, and the verification compares it — a leftover
  root-mode change now reports restored False instead of True.
  One new test proves it: a failed run restores a chmod'ed app
  directory's original mode.

## v2.6.0 — 2026-10-26

- Safety fix, no behaviour change for passing runs: the restore is now
  verified instead of assumed. After the restore, the app tree is
  re-snapshotted and compared with the original; `restored` in the
  report is True only when that comparison is clean, and any leftover
  paths are named in a new `restore_mismatch` list (and printed as a
  WARNING by the CLI) instead of being silently claimed clean. This
  closes the last untrue-claim hole in the chain v2.2–v2.5 closed for
  contents, shape, modes, and special files: a fixer-created directory
  that cannot be removed because it holds only cache artefacts the
  snapshot excludes used to survive the "restore" while the report
  still said restored. A restore that raises is also caught, so it
  still produces a report naming the problem instead of crashing.
  One documented exception: a snapshot socket or device node a fixer
  deleted cannot be recreated, so it is left absent by design and does
  not count as a mismatch; a snapshot FIFO is recreated. One new test
  proves it: a failed run whose restore cannot finish reports
  restored False and names the leftover directory, while the
  restorable files are still put back.

## v2.5.0 — 2026-10-26

- Safety fix, no behaviour change for passing runs: the snapshot and
  restore now cover non-regular app entries — FIFOs, sockets, device
  nodes — as well as files, directories, symlinks, and modes. Two
  v2.4 holes are closed. A fixer-created FIFO used to survive the
  "restore" while the report still claimed the original code was
  restored; it is now removed, and creating one counts as a fixer
  change, so it gets a fresh full-suite run like any other fix. And
  a snapshot file replaced by a FIFO used to hang the restore
  outright — restore tried to write through the FIFO, which blocks
  forever waiting for a reader, so no report was ever produced. The
  restore now unlinks a special file before writing a snapshot file
  back, and a snapshot FIFO that a fixer deleted or replaced is
  recreated (sockets and device nodes cannot be recreated, so a
  replaced one is removed rather than faked with the wrong type).
  Special files are recorded by type via lstat and are never opened
  or followed. Two new tests prove both halves: a failed run removes
  a fixer-created FIFO, and a failed run puts back a file a fixer
  replaced with a FIFO instead of hanging.

## v2.4.0 — 2026-10-26

- Safety fix, no behaviour change for passing runs: the snapshot and
  restore now cover file and directory permission bits as well as
  contents and shape. A fixer that chmod'ed a file (stripping its exec
  bit, or making it world-writable) used to leave that change behind
  on final failure, and a fixer that made a file or directory
  read-only could break the restore of its contents — now the mode is
  put back exactly, with directories made owner-writable only while
  their deleted contents are recreated. A mode-only change also counts
  as a fixer change, so it gets a fresh full-suite run like any other
  fix. One new test proves it: a failed run restores a chmod'ed file's
  contents and its original mode.

- Safety fix, no behaviour change for passing runs: the snapshot and
  restore now cover the app's shape as well as its file contents —
  empty directories and symlinks. A fixer-created empty directory or
  symlink is now removed on final failure, and an empty directory the
  fixer deleted is put back, so "original code restored" is now true
  for the whole app tree, not just its files. Symlinks are recorded
  by target (never followed), so restoring a symlink cannot write
  through it to a file outside the app, and a file replaced by a
  directory (or vice versa) is swapped back instead of crashing the
  restore. Cache/VCS directories stay excluded. One new test proves
  it: a failed run restores a deleted empty directory and removes a
  fixer-created directory and symlink.

## v2.2.0 — 2026-10-26

- Safety fix, no behaviour change for Python-only apps: the snapshot
  and restore now cover ALL app files, not just `*.py`. A fixer that
  changes a data/config file now counts as a change (so the loop
  re-runs the suite instead of stopping early), and on final failure
  those files are restored and fixer-created files are deleted — so
  "original code restored" is now true for the whole app, not just
  its Python files. Cache/VCS directories (`__pycache__`,
  `.pytest_cache`, `.git`, etc.) stay excluded. Two new tests prove
  both halves: a data-file-only fix passes, and a failed run restores
  a changed data file and removes a fixer-created file.

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
