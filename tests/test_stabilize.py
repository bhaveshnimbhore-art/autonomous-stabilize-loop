"""Tests for the loop itself — the loop must prove it works, not claim it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABILIZE = ROOT / "stabilize.py"


def run_loop(app_dir, tmp):
    report = tmp / "report.json"
    result = subprocess.run(
        [sys.executable, str(STABILIZE), str(app_dir), "--report", str(report)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    data = json.loads(report.read_text()) if report.exists() else {}
    return result, data


def test_clean_app_passes_first_try(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(ROOT / "demo-app", app)
    result, data = run_loop(app, tmp_path)
    assert result.returncode == 0
    assert data["stable"] is True
    assert data["attempts"] == 1


def test_buggy_app_is_fixed_and_passes(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(ROOT / "demo-app-buggy", app)
    result, data = run_loop(app, tmp_path)
    assert result.returncode == 0
    assert data["stable"] is True
    assert "subtotal + tax + tax" not in (app / "app.py").read_text()
    # The fixed app really passes its own full suite:
    check = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=app, capture_output=True, text=True
    )
    assert check.returncode == 0


def test_unfixable_app_is_restored_not_downgraded(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    original = (app / "app.py").read_text()
    result, data = run_loop(app, tmp_path)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == original
    assert "test_total" in data["failing"]


def test_missing_app_dir_is_usage_error(tmp_path):
    result = subprocess.run(
        [sys.executable, str(STABILIZE), str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
