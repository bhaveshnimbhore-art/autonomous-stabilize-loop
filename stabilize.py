#!/usr/bin/env python3
"""Write-test-stabilize loop, v2.7 — free, no vendor lock-in.

Usage:
    python3 stabilize.py demo-app
    python3 stabilize.py demo-app-buggy --max-attempts 5 --timeout 60 --report report.json
    python3 stabilize.py app --fixer-script ./my_fixer.py

Rules enforced by this loop (from QA_JUDGE_PROMPT.md, not just documented):
  1. Success = the FULL test suite passes. Compiling alone is never success.
  2. Every attempt re-runs the FULL suite, never just the one failing test.
  3. If the loop cannot stabilize the app, it RESTORES the original code
     and reports the exact failing test + error line. It never leaves the
     app worse than it found it ("without downgrading", enforced in code).
  4. A fixer is pluggable: pass a script that takes (app_dir, error_log_file)
     and exits 0 only if it changed a file. The built-in fixer is only a
     demo for one known bug class — replace it for real work, do not
     pretend it fixes everything.

Exit codes: 0 = stable (full suite passed), 1 = could not stabilize,
2 = usage / app error (missing dir, no tests found, timeout on first run
    with no fix attempted is still reported as 1, not hidden).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# --- demo fixer: one known bug class, explicitly limited -------------------
# Original v1 only patched "return subtotal + tax + tax". v2 keeps that
# patch and generalises it slightly (same bug, different spacing/names),
# but it still claims nothing more than that. Real fixers go in
# --fixer-script, which is checked the same way: did it change a file,
# and does the FULL suite pass afterwards?


def _demo_fix(app_dir: Path, error_log: str) -> bool:
    target = app_dir / "app.py"
    if not target.exists():
        return False
    code = target.read_text()
    new = re.sub(
        r"return\s+subtotal\s*\+\s*tax\s*\+\s*tax\b.*",
        "return subtotal + tax",
        code,
    )
    if new != code:
        target.write_text(new)
        return True
    return False


def run_fixer(app_dir: Path, error_log: str, fixer_script: str | None) -> bool:
    """Return True only if the fixer actually changed a file."""
    before = _snapshot(app_dir)
    if fixer_script:
        script = Path(fixer_script)
        if not script.exists():
            raise FileNotFoundError(f"fixer script not found: {fixer_script}")
        with tempfile.NamedTemporaryFile(
            "w", suffix=".log", delete=False
        ) as log_file:
            log_file.write(error_log)
            log_path = log_file.name
        try:
            result = subprocess.run(
                [sys.executable, str(script), str(app_dir), log_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            changed = _snapshot(app_dir) != before
            return result.returncode == 0 and changed
        except subprocess.TimeoutExpired:
            return False
        finally:
            Path(log_path).unlink(missing_ok=True)
    return _demo_fix(app_dir, error_log)


# Directories whose contents are test-run / VCS artefacts, never app
# content: snapshotting them would bloat the snapshot and restoring
# them could fight the test runner or version control. Everything else
# — .py, data, config, text, assets — is app content and is covered,
# so a fixer that touches a non-Python file is judged and, on failure,
# rolled back exactly like a .py change. (v2.1 snapshotted *.py only,
# so a fixer changing e.g. data.txt left that change behind on failure
# even though the report claimed the original code was restored.)
#
# v2.3 extends the same promise to the app's shape, not just its file
# contents: the snapshot also records empty directories and symlinks.
# v2.2 left both behind — a fixer that created an empty directory or a
# symlink, or deleted an empty directory, survived the "restore", so
# the app after a failed run was not the app the loop started with.
#
# v2.4 extends it to permission bits: the snapshot also records each
# file and directory's mode. v2.3 restored contents and shape but not
# modes — a fixer that chmod'ed a file (stripping its exec bit, or
# making it world-writable) left that change behind, and a fixer that
# made a file read-only could even break a later restore of its
# contents. Modes now count as a change too, so a fixer that only
# chmods is judged by a fresh full-suite run like any other fixer.
# Entries are ("file", bytes, mode), ("dir", None, mode), or
# ("link", target, None). Modes are the low permission bits only.
#
# v2.5 extends it to non-regular entries: FIFOs, sockets, device
# nodes. v2.4 recorded only files, directories, and symlinks, so a
# fixer-created FIFO survived the "restore" (the report still claimed
# the original code was restored), and a snapshot file replaced by a
# FIFO was worse: restore tried to write_bytes() through the FIFO,
# which blocks forever waiting for a reader — the loop hung with no
# report at all. Entries for these are ("other", file_type, mode),
# recorded by type via lstat and never opened or followed, so a
# fixer-created one counts as a change and is removed on failure,
# and a file replaced by one is unlinked before it is written back.
#
# v2.7 extends it to the app directory itself: the snapshot also
# records the root directory's mode, under the key ".". v2.6 recorded
# every entry inside the app but not the app directory, so a fixer
# that chmod'ed the app directory (e.g. 0o750 -> 0o777) left that
# change behind while the report still claimed the original code
# was restored — the same untrue-claim hole v2.2-v2.6 closed for
# contents, shape, modes, and special files. The root entry flows
# through the same machinery as any other directory: a root-only
# chmod counts as a fixer change, the restore makes the root
# owner-writable first (so a fixer that made it read-only cannot
# block the content restore), and the exact original mode is put
# back in the final pass — and the verification compares it, so a
# leftover root-mode change now reports restored False.
_IGNORED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    "node_modules",
    ".venv",
    "venv",
}


def _snapshot(app_dir: Path) -> dict[str, tuple]:
    """Snapshot every app entry — file, directory, symlink, or special
    file (FIFO/socket/device) — by relative path, plus the app
    directory itself under "." (its mode only, v2.7)."""
    out: dict[str, tuple] = {}
    # The app directory itself, first: the restore makes it
    # owner-writable before touching contents (see _restore), and
    # the final pass puts back its exact mode. Guarded: if the app
    # directory was deleted outright, there is no root mode to record
    # and the entries below still describe what must come back.
    try:
        if app_dir.is_dir():
            out["."] = ("dir", None, app_dir.stat().st_mode & 0o777)
    except OSError:
        pass
    for path in sorted(app_dir.rglob("*")):
        if _IGNORED_PARTS.intersection(path.parts):
            continue
        rel = str(path.relative_to(app_dir))
        if path.is_symlink():
            # Checked first: a symlink to a file also reports is_file(),
            # and a broken symlink reports neither is_file() nor is_dir().
            out[rel] = ("link", os.readlink(path), None)
        elif path.is_dir():
            out[rel] = ("dir", None, path.stat().st_mode & 0o777)
        elif path.is_file():
            out[rel] = ("file", path.read_bytes(), path.stat().st_mode & 0o777)
        else:
            # FIFO, socket, device node, or anything else non-regular:
            # recorded by type, never opened, read, or followed.
            mode = os.lstat(path).st_mode
            out[rel] = ("other", stat.S_IFMT(mode), mode & 0o777)
    return out


def _restore(app_dir: Path, snapshot: dict[str, tuple]) -> None:
    current = _snapshot(app_dir)
    # 1. Remove files, symlinks, and special files the fixer created
    #    (dirs last: a created directory may still hold a created
    #    file at this point). Unlinking a FIFO/socket is safe; it is
    #    never opened, so this cannot block.
    for name in current:
        if name not in snapshot and current[name][0] in ("file", "link", "other"):
            (app_dir / name).unlink(missing_ok=True)
    # 2. Remove directories the fixer created, deepest first, only if
    #    empty (they are, once step 1 removed their files).
    for name in sorted(
        (n for n in current if n not in snapshot and current[n][0] == "dir"),
        key=lambda n: n.count("/"),
        reverse=True,
    ):
        try:
            (app_dir / name).rmdir()
        except OSError:
            pass
    # 3. Put back everything from the snapshot, including directories
    #    the fixer deleted and symlinks it replaced or removed. A path
    #    that is currently a symlink must be unlinked before writing a
    #    regular file, or write_bytes would follow it out of the app.
    #    Directory modes are restored in two passes: here each restored
    #    directory is made owner-writable first, so files and links the
    #    fixer deleted can be recreated inside it even if the fixer
    #    made it read-only; step 4 then sets the exact original mode.
    #    Likewise a file the fixer made read-only is made writable
    #    before it is overwritten, then set back to its original mode.
    for name, (kind, data, mode) in snapshot.items():
        path = app_dir / name
        if kind == "dir":
            # A snapshot directory replaced by a file, symlink, or
            # special file (e.g. a FIFO) must be unlinked first, or
            # mkdir(exist_ok=True) raises FileExistsError.
            if path.is_symlink() or (os.path.lexists(path) and not path.is_dir()):
                path.unlink()
            path.mkdir(parents=True, exist_ok=True)
            if mode is not None:
                os.chmod(path, mode | 0o300)
            continue
        # A fixer may have replaced a file/symlink with a directory of
        # the same name; by now its contents are removed, so it is
        # empty and can be swapped back. (If it is not empty, rmdir
        # fails and the write below fails loudly rather than deleting
        # unknown content.)
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        if kind == "link":
            if path.is_symlink() and os.readlink(path) == data:
                continue
            # A snapshot symlink replaced by a directory is swapped
            # back (its created contents are already gone after
            # steps 1-2); a special file is unlinked, never followed.
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            elif path.is_symlink() or os.path.lexists(path):
                path.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(data)
        elif kind == "other":
            # A snapshot special file: never opened or followed. The
            # same type already there is kept, with its mode put
            # back. Anything else is removed; a FIFO is recreated
            # (a socket or device node cannot be recreated by this
            # loop, so it is left absent rather than faked with the
            # wrong type).
            if os.path.lexists(path):
                if stat.S_IFMT(os.lstat(path).st_mode) == data:
                    if mode is not None:
                        os.chmod(path, mode)
                    continue
                try:
                    if path.is_dir() and not path.is_symlink():
                        path.rmdir()
                    else:
                        path.unlink()
                except OSError:
                    pass
            if not os.path.lexists(path) and data == stat.S_IFIFO:
                path.parent.mkdir(parents=True, exist_ok=True)
                os.mkfifo(path)
                if mode is not None:
                    os.chmod(path, mode)
            continue
        else:
            # A snapshot file replaced by a special file (FIFO,
            # socket) must be unlinked before it is written: writing
            # through a FIFO would block forever waiting for a
            # reader, and writing through a device node could touch
            # hardware. Directories were handled above.
            if path.is_symlink() or (
                os.path.lexists(path) and not path.is_dir() and not path.is_file()
            ):
                path.unlink()
            elif path.is_file():
                os.chmod(path, 0o600)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if mode is not None:
                os.chmod(path, mode)
    # 4. Exact directory modes, now that their contents are back.
    for name, (kind, data, mode) in snapshot.items():
        if kind == "dir" and mode is not None:
            os.chmod(app_dir / name, mode)


# v2.6 verifies the restore instead of assuming it: after _restore,
# the app tree is re-snapshotted and compared with the original. A
# restore that cannot finish — e.g. a fixer-created directory that
# cannot be removed because it holds cache artefacts the snapshot
# excludes — used to be reported as restored=True anyway, the same
# class of untrue claim v2.2–v2.5 closed for contents, shape, modes,
# and special files. Now `restored` is True only when the comparison
# is clean, and any leftover paths are named in `restore_mismatch`
# so the failure can be fixed by hand instead of trusted blindly.
# One documented exception: a snapshot socket or device node that a
# fixer deleted cannot be recreated, so it is left absent by design
# and does not count as a mismatch; a snapshot FIFO is recreated.
def _restore_mismatches(app_dir: Path, snapshot: dict[str, tuple]) -> list[str]:
    """Paths where the app tree still differs from `snapshot` after a
    restore — missing, changed, or extra entries, sorted. An absent
    snapshot socket/device node is not a mismatch (see note above)."""
    try:
        current = _snapshot(app_dir)
    except Exception as exc:  # verification must never crash the report
        return [f"restore verification failed: {exc}"]
    mismatches: list[str] = []
    for name, entry in snapshot.items():
        if name not in current:
            if entry[0] == "other" and entry[1] != stat.S_IFIFO:
                continue
            mismatches.append(name)
        elif current[name] != entry:
            mismatches.append(name)
    for name in current:
        if name not in snapshot:
            mismatches.append(name)
    return sorted(mismatches)


def run_tests(app_dir: Path, timeout: int) -> tuple[int, str, str, bool]:
    """Run the FULL suite. Returns (returncode, stdout, stderr, timed_out)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, out, err, True


def failing_test_name(output: str) -> str:
    """Extract the first failing test / error line, or '' if none found."""
    for line in output.splitlines():
        if line.startswith("FAILED "):
            return line.strip()
        if re.match(r".*::test_\w+ .*", line) and ("FAILED" in line or "Error" in line):
            return line.strip()
    # pytest summary lines: "FAILED test_app.py::test_total - assert ..."
    for line in output.splitlines():
        if "FAILED" in line:
            return line.strip()
    return ""


def failure_detail(output: str, max_lines: int = 12) -> str:
    """Concise error excerpt for a failed run: the FAILED line plus the
    pytest 'E' assertion/traceback lines (the actual error, not just the
    test name). Falls back to the last non-empty output lines so a
    failure with no 'E' lines (collection/import errors, timeouts) still
    reports something concrete instead of a bare summary line."""
    lines = [line.strip() for line in output.splitlines()]
    failed = [line for line in lines if line.startswith("FAILED ") or line.startswith("ERROR ")]
    err_lines = [line for line in lines if line.startswith("E ")]
    detail = failed + err_lines
    if detail:
        return "\n".join(detail[:max_lines])
    tail = [line for line in lines if line][-5:]
    return "\n".join(tail)


def compile_check(app_dir: Path) -> bool:
    """py_compile only — used as a sanity check, NEVER as a pass criterion."""
    for path in app_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True,
        )
        if result.returncode != 0:
            return False
    return True


def stabilize(
    app_dir: Path,
    max_attempts: int = 5,
    timeout: int = 120,
    fixer_script: str | None = None,
) -> dict:
    if not app_dir.is_dir():
        return {"stable": False, "error": f"app dir not found: {app_dir}", "attempts": 0}
    if not list(app_dir.rglob("test_*.py")):
        return {"stable": False, "error": f"no test_*.py found in {app_dir}", "attempts": 0}

    original = _snapshot(app_dir)
    attempts: list[dict] = []
    last_failure = ""
    last_detail = ""

    for attempt in range(1, max_attempts + 1):
        returncode, stdout, stderr, timed_out = run_tests(app_dir, timeout)
        output = stdout + stderr
        failure = failing_test_name(output) or ("timeout" if timed_out else "")
        detail = failure_detail(output) if returncode != 0 else ""
        attempts.append(
            {
                "attempt": attempt,
                "returncode": returncode,
                "timed_out": timed_out,
                "failing": failure,
                "detail": detail,
            }
        )
        if returncode == 0:
            return {
                "stable": True,
                "attempts": attempt,
                "attempt_log": attempts,
                "failing": "",
                "failure_detail": "",
                "restored": False,
            }
        last_failure = failure or output.strip().splitlines()[-1] if output.strip() else "unknown failure"
        last_detail = detail
        print(f"Attempt {attempt}/{max_attempts}: FAILED — {last_failure}", file=sys.stderr)
        if attempt == max_attempts:
            break
        try:
            changed = run_fixer(app_dir, output, fixer_script)
        except Exception as exc:  # fixer must never crash the loop silently
            print(f"Fixer error: {exc}", file=sys.stderr)
            changed = False
        if not changed:
            print("Fixer could not patch the error. Stopping.", file=sys.stderr)
            break
        print("Fix applied, re-running FULL suite...", file=sys.stderr)

    # Not stable: restore original code — never leave a downgrade behind.
    # The restore is then verified, not assumed: `restored` is True only
    # if a fresh snapshot matches the original, and any leftover paths
    # are named in `restore_mismatch` instead of silently claimed clean.
    try:
        _restore(app_dir, original)
        restore_error = ""
    except Exception as exc:  # a failed restore must still produce a report
        restore_error = str(exc)
        print(f"Restore error: {exc}", file=sys.stderr)
    mismatches = _restore_mismatches(app_dir, original)
    result: dict = {
        "stable": False,
        "attempts": len(attempts),
        "attempt_log": attempts,
        "failing": last_failure,
        "failure_detail": last_detail,
        "restored": not mismatches,
        "restore_mismatch": mismatches,
    }
    if restore_error:
        result["restore_error"] = restore_error
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write-test-stabilize loop (v2)")
    parser.add_argument("app_dir", nargs="?", default="demo-app")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=120, help="seconds per pytest run")
    parser.add_argument("--fixer-script", default=None, help="pluggable fixer script")
    parser.add_argument("--report", default=None, help="write JSON report here")
    args = parser.parse_args(argv)

    app_dir = Path(args.app_dir)
    result = stabilize(app_dir, args.max_attempts, args.timeout, args.fixer_script)
    result["app_dir"] = str(app_dir)
    result["compile_ok"] = compile_check(app_dir)
    if args.report:
        Path(args.report).write_text(json.dumps(result, indent=2))
    if result["stable"]:
        print(f"STABLE VERSION PRODUCED! All tests passed after {result['attempts']} attempt(s).")
        return 0
    print(f"NOT STABLE. Original code restored: {result.get('restored')}.")
    if result.get("restore_mismatch"):
        print("WARNING: restore incomplete — still differing:")
        for name in result["restore_mismatch"]:
            print(f"  {name}")
    print(f"Failing: {result.get('failing') or result.get('error', 'unknown')}")
    if result.get("failure_detail"):
        print("Detail:")
        print(result["failure_detail"])
    return 1 if "error" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
