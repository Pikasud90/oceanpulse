"""Cross-platform behaviour and startup edge cases.

These are the failures that only appear on a fresh device, on the other
operating system, or when something has gone wrong — exactly the cases that a
happy-path test run never reaches.
"""

from __future__ import annotations

import socket
import sqlite3
import sys
from pathlib import Path

import pytest

from oceanpulse.fileops import ReplaceInUseError, atomic_replace, remove_quietly
from oceanpulse.models import marine_coordinates

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Zero is a real coordinate
# ---------------------------------------------------------------------------


def test_zero_longitude_is_a_place_not_a_missing_value():
    """The Greenwich meridian is not "no data".

    `marine_longitude or longitude` treats 0.0 as falsy and silently reverts to
    the harbour coordinate — discarding the resolved offshore cell that the
    whole resolution step exists to find, and doing it invisibly because the
    reverted value is still plausible. Tema in Ghana sits within 0.02° of 0°.
    """
    port = {
        "latitude": 5.63,
        "longitude": 0.02,
        "marine_latitude": 5.5,
        "marine_longitude": 0.0,
    }
    latitude, longitude = marine_coordinates(port)
    assert longitude == 0.0, "a resolved cell at 0° must survive"
    assert latitude == 5.5


def test_zero_latitude_survives_too():
    """The equator, for the same reason."""
    port = {
        "latitude": 0.31,
        "longitude": 9.45,
        "marine_latitude": 0.0,
        "marine_longitude": 9.4,
    }
    latitude, _ = marine_coordinates(port)
    assert latitude == 0.0


def test_missing_marine_cell_falls_back_to_the_harbour():
    port = {"latitude": 51.9, "longitude": 4.48,
            "marine_latitude": None, "marine_longitude": None}
    assert marine_coordinates(port) == (51.9, 4.48)

    absent = {"latitude": 51.9, "longitude": 4.48}
    assert marine_coordinates(absent) == (51.9, 4.48)


# ---------------------------------------------------------------------------
# Atomic replacement across platforms
# ---------------------------------------------------------------------------


def test_atomic_replace_swaps_a_file(tmp_path):
    target = tmp_path / "ports.sqlite"
    target.write_bytes(b"old")
    built = tmp_path / "ports.sqlite.building"
    built.write_bytes(b"new")

    atomic_replace(built, target)

    assert target.read_bytes() == b"new"
    assert not built.exists()


def test_atomic_replace_works_while_a_reader_holds_the_file(tmp_path):
    """Rebuilding the gazetteer while the interface is running must work.

    POSIX allows replacing an open file; Windows refuses and the helper moves
    the old file aside instead. Either way the new content must land.
    """
    target = tmp_path / "ports.sqlite"
    connection = sqlite3.connect(target)
    connection.execute("CREATE TABLE t (a)")
    connection.commit()

    built = tmp_path / "ports.sqlite.building"
    built.write_bytes(b"replacement")
    try:
        atomic_replace(built, target)
    except ReplaceInUseError:
        pytest.fail("helper should fall back to moving the old file aside")
    finally:
        connection.close()

    assert target.read_bytes() == b"replacement"


def test_atomic_replace_never_destroys_the_new_file_on_failure(tmp_path, monkeypatch):
    """If the swap is impossible, the built file must survive.

    It cost a minute of downloading; losing it and making the user start again
    would be the worst possible outcome.
    """
    import os

    target = tmp_path / "ports.sqlite"
    target.write_bytes(b"old")
    built = tmp_path / "ports.sqlite.building"
    built.write_bytes(b"new")

    def always_locked(*args, **kwargs):
        raise PermissionError(32, "in use")

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr(Path, "replace", lambda self, other: always_locked())

    with pytest.raises(ReplaceInUseError) as caught:
        atomic_replace(built, target)

    assert built.exists(), "the freshly built file must not be lost"
    # The message has to tell the user what to do about it.
    assert "stop OceanPulse" in str(caught.value)


def test_remove_quietly_tolerates_a_missing_file(tmp_path):
    remove_quietly(tmp_path / "never-existed")


# ---------------------------------------------------------------------------
# Startup guards
# ---------------------------------------------------------------------------


def _run_module():
    sys.path.insert(0, str(PROJECT_ROOT))
    import run  # noqa: PLC0415

    return run


def test_port_preflight_detects_a_listener():
    run = _run_module()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert run.port_is_free("127.0.0.1", port) is False
    finally:
        listener.close()
    assert run.port_is_free("127.0.0.1", port) is True


def test_corrupt_database_exits_with_guidance(tmp_path, capsys):
    """A damaged file is a user problem with a user fix, not a stack trace."""
    run = _run_module()
    broken = tmp_path / "oceanpulse.sqlite"
    broken.write_bytes(b"not a database at all" * 50)

    with pytest.raises(SystemExit) as exit_info:
        run.open_storage_or_explain(broken)

    assert exit_info.value.code == 1
    message = capsys.readouterr().err
    assert "could not be opened" in message
    assert ".broken" in message, "must show the user the exact recovery step"


def test_fts5_probe_does_not_raise():
    """The probe must report, never crash, whatever SQLite it finds."""
    run = _run_module()
    run.check_sqlite_features()


# ---------------------------------------------------------------------------
# Launcher parity
# ---------------------------------------------------------------------------


def test_both_launchers_exist_and_take_the_same_arguments():
    assert (PROJECT_ROOT / "run.sh").is_file()
    assert (PROJECT_ROOT / "run.bat").is_file()
    shell = (PROJECT_ROOT / "run.sh").read_text()
    batch = (PROJECT_ROOT / "run.bat").read_text()
    # Both must forward every argument through to run.py unchanged.
    assert 'python run.py "$@"' in shell
    assert "python run.py %*" in batch


def test_both_launchers_gate_dependency_install_on_content_not_mtime():
    """A git checkout rewrites mtimes; hashing avoids pointless reinstalls.

    They must also agree, or Windows silently runs stale packages after a pull
    while macOS reinstalls — which is how the two platforms drift apart.
    """
    shell = (PROJECT_ROOT / "run.sh").read_text()
    batch = (PROJECT_ROOT / "run.bat").read_text()
    assert "shasum" in shell or "sha256sum" in shell
    assert "certutil" in batch and "SHA256" in batch
    assert ".requirements-stamp" in shell and ".requirements-stamp" in batch


def test_windows_launcher_sets_a_utf8_console():
    """cp1252 plus a non-ASCII path is a UnicodeEncodeError inside logging."""
    batch = (PROJECT_ROOT / "run.bat").read_text()
    assert "chcp 65001" in batch


def test_both_launchers_quote_paths_containing_spaces():
    """This project already lives under a path with a space in it."""
    shell = (PROJECT_ROOT / "run.sh").read_text()
    batch = (PROJECT_ROOT / "run.bat").read_text()
    assert 'cd "$(dirname "$0")"' in shell
    assert 'cd /d "%~dp0"' in batch


def test_service_installers_exist_for_both_platforms():
    scripts = PROJECT_ROOT / "scripts"
    for name in (
        "setup_service.sh",
        "setup_service.bat",
        "uninstall_service.sh",
        "uninstall_service.bat",
        "init_gazetteer.py",
    ):
        assert (scripts / name).is_file(), f"missing {name}"


def test_shell_scripts_are_committed_executable():
    """A fresh clone on macOS or Linux should not need chmod first."""
    import subprocess

    listing = subprocess.run(
        ["git", "ls-files", "-s", "run.sh", "scripts/setup_service.sh",
         "scripts/uninstall_service.sh"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    for line in listing.strip().splitlines():
        mode = line.split()[0]
        assert mode == "100755", f"not executable in git: {line}"
