#!/usr/bin/env python3
"""Write-test-stabilize loop, v2 — free, no vendor lock-in.

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
import re
import shutil
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


def _snapshot(app_dir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        out[str(path.relative_to(app_dir))] = path.read_bytes()
    return out


def _restore(app_dir: Path, snapshot: dict[str, bytes]) -> None:
    current = _snapshot(app_dir)
    for name in current:
        if name not in snapshot:
            (app_dir / name).unlink(missing_ok=True)
    for name, data in snapshot.items():
        path = app_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


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
    _restore(app_dir, original)
    return {
        "stable": False,
        "attempts": len(attempts),
        "attempt_log": attempts,
        "failing": last_failure,
        "failure_detail": last_detail,
        "restored": True,
    }


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
    print(f"Failing: {result.get('failing') or result.get('error', 'unknown')}")
    if result.get("failure_detail"):
        print("Detail:")
        print(result["failure_detail"])
    return 1 if "error" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
