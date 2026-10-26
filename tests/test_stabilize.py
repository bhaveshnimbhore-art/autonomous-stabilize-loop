"""Tests for the loop itself — the loop must prove it works, not claim it."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABILIZE = ROOT / "stabilize.py"


def run_loop(app_dir, tmp, fixer_script=None):
    report = tmp / "report.json"
    cmd = [sys.executable, str(STABILIZE), str(app_dir), "--report", str(report)]
    if fixer_script is not None:
        cmd += ["--fixer-script", str(fixer_script)]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    data = json.loads(report.read_text()) if report.exists() else {}
    return result, data


def write_fixer(tmp, body):
    script = tmp / "fixer.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "app_dir = Path(sys.argv[1])\n" + body
    )
    return script


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


def test_unfixable_report_carries_error_detail(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    result, data = run_loop(app, tmp_path)
    assert result.returncode != 0
    assert "assert 0 == 220" in data["failure_detail"]
    assert data["attempt_log"][-1]["detail"] == data["failure_detail"]


def test_nonpy_fixer_change_counts_and_passes(tmp_path):
    # A fixer that only touches a data file used to count as "no change"
    # (snapshot was *.py only), so the loop stopped without a second run.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text(
        "from pathlib import Path\n\n"
        "def value():\n"
        "    return int((Path(__file__).parent / 'data.txt').read_text().strip())\n"
    )
    (app / "test_app.py").write_text(
        "from app import value\n\ndef test_value():\n    assert value() == 42\n"
    )
    (app / "data.txt").write_text("0\n")
    fixer = write_fixer(tmp_path, "(app_dir / 'data.txt').write_text('42\\n')\n")
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode == 0
    assert data["stable"] is True
    assert (app / "data.txt").read_text().strip() == "42"


def test_nonpy_changes_restored_on_failure(tmp_path):
    # On failure the restore must put back non-Python files too, and
    # delete files the fixer created — otherwise the "no downgrade"
    # promise is broken for any app with data/config files.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    (app / "data.txt").write_text("original\n")
    fixer = write_fixer(
        tmp_path,
        "(app_dir / 'data.txt').write_text('changed\\n')\n"
        "(app_dir / 'extra.txt').write_text('created by fixer\\n')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == "def total(price, qty, tax_rate):\n    return 0\n"
    assert (app / "data.txt").read_text() == "original\n"
    assert not (app / "extra.txt").exists()


def test_dir_and_symlink_shape_restored_on_failure(tmp_path):
    # v2.2 restored file contents but left the app's shape changed: a
    # fixer-created empty directory or symlink survived, and an empty
    # directory the fixer deleted stayed deleted — so "original code
    # restored" was not true for the whole app.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    (app / "keep").mkdir()
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "import os\n"
        "(app_dir / 'keep').rmdir()\n"
        "(app_dir / 'newdir').mkdir()\n"
        "os.symlink('app.py', app_dir / 'link.py')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == original
    assert (app / "keep").is_dir()
    assert not os.path.lexists(app / "newdir")
    assert not os.path.lexists(app / "link.py")


def test_file_modes_restored_on_failure(tmp_path):
    # v2.3 restored file contents and app shape but not permission
    # bits: a fixer that chmod'ed a file left the mode changed even
    # though the report claimed the original code was restored — and a
    # fixer that made a file read-only could break the content
    # restore itself. Both must come back exactly.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    os.chmod(app / "app.py", 0o640)
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "import os\n"
        "os.chmod(app_dir / 'app.py', 0o777)\n"
        "(app_dir / 'app.py').write_text('def total(price, qty, tax_rate):\\n    return 1\\n')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == original
    assert (app / "app.py").stat().st_mode & 0o777 == 0o640


def test_fixer_created_fifo_removed_on_failure(tmp_path):
    # v2.4 recorded only files, directories, and symlinks, so a
    # fixer-created FIFO survived the "restore" even though the
    # report claimed the original code was restored.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "import os\n"
        "(app_dir / 'app.py').write_text('def total(price, qty, tax_rate):\\n    return 1\\n')\n"
        "if not os.path.lexists(app_dir / 'pipe.fifo'):\n"
        "    os.mkfifo(app_dir / 'pipe.fifo')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == original
    assert not os.path.lexists(app / "pipe.fifo")


def test_file_replaced_by_fifo_restored_on_failure(tmp_path):
    # v2.4 hung here, not just leaked: restore tried to write_bytes()
    # through the FIFO a fixer had put in place of app.py, which
    # blocks forever waiting for a reader — no report, no restore.
    # The loop must unlink the FIFO first and put the file back.
    import stat

    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "import os\n"
        "if os.path.lexists(app_dir / 'app.py'):\n"
        "    if (app_dir / 'app.py').is_dir() and not (app_dir / 'app.py').is_symlink():\n"
        "        (app_dir / 'app.py').rmdir()\n"
        "    else:\n"
        "        os.unlink(app_dir / 'app.py')\n"
        "os.mkfifo(app_dir / 'app.py')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").is_file() and not (app / "app.py").is_symlink()
    assert not stat.S_ISFIFO(os.lstat(app / "app.py").st_mode)
    assert (app / "app.py").read_text() == original


def test_incomplete_restore_is_reported_not_claimed(tmp_path):
    # v2.5 reported restored=True unconditionally, even when the
    # restore could not finish: a fixer-created directory holding
    # only cache artefacts (excluded from the snapshot) cannot be
    # removed by rmdir, so it survives the "restore". v2.6 verifies
    # the restore and reports the leftover path instead of claiming
    # the original code was restored.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "(app_dir / 'app.py').write_text('def total(price, qty, tax_rate):\\n    return 1\\n')\n"
        "(app_dir / 'newdir' / '__pycache__').mkdir(parents=True)\n"
        "(app_dir / 'newdir' / '__pycache__' / 'junk.pyc').write_bytes(b'junk')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is False
    assert "newdir" in data["restore_mismatch"]
    assert (app / "app.py").read_text() == original


def test_app_root_dir_mode_restored_on_failure(tmp_path):
    # v2.6 snapshotted every entry inside the app but not the app
    # directory itself, so a fixer that chmod'ed the app directory
    # left that change behind while the report still claimed the
    # original code was restored. The root mode must come back
    # exactly, like any other directory's mode.
    app = tmp_path / "app"
    app.mkdir()
    (app / "app.py").write_text("def total(price, qty, tax_rate):\n    return 0\n")
    (app / "test_app.py").write_text(
        "from app import total\n\ndef test_total():\n    assert total(100, 2, 0.10) == 220\n"
    )
    os.chmod(app, 0o750)
    original = (app / "app.py").read_text()
    fixer = write_fixer(
        tmp_path,
        "import os\n"
        "os.chmod(app_dir, 0o777)\n"
        "(app_dir / 'app.py').write_text('def total(price, qty, tax_rate):\\n    return 1\\n')\n",
    )
    result, data = run_loop(app, tmp_path, fixer_script=fixer)
    assert result.returncode != 0
    assert data["stable"] is False
    assert data["restored"] is True
    assert (app / "app.py").read_text() == original
    assert app.stat().st_mode & 0o777 == 0o750


def test_missing_app_dir_is_usage_error(tmp_path):
    result = subprocess.run(
        [sys.executable, str(STABILIZE), str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
