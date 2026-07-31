"""Offline mode (H-1), the state lock (R-3) and prefs rollback (H-4)."""

import os
import sys
import zipfile

import pytest

import gvm.main as main
from gvm.cache import Cache, CacheEntry, Cacher
from gvm.lockfile import StateLock, _pid_is_running
from gvm.prefs_backup.backup_restorer import _atomic_write_with_rollback


# --- offline (H-1) ----------------------------------------------------------

def test_offline_makes_no_network_calls(tmp_home, monkeypatch):
    """`--offline` used to still fire the implicit update check."""
    calls: list[str] = []

    def spy(url, *a, **k):
        calls.append(url)
        raise AssertionError("network call while offline")

    monkeypatch.setattr(main.ghttp, "get", spy)
    monkeypatch.setattr(sys, "argv", ["gvm", "--offline", "install", "Ghidra_11.4_build"])

    try:
        main.main()
    except SystemExit:
        pass

    assert calls == [], f"--offline still made network calls: {calls}"


def test_online_update_check_is_allowed_for_install():
    assert main._allow_update_check("install") is True


@pytest.mark.parametrize("cmd", ["locate", "list", "prefs", "gui", "settings"])
def test_update_check_skipped_for_local_commands(cmd):
    assert main._allow_update_check(cmd) is False


# --- state lock (R-3) -------------------------------------------------------

def test_lock_acquire_and_release(tmp_path):
    lock = StateLock(tmp_path / "gvm.lock", label="gui")
    assert lock.acquire() is True
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_second_acquire_by_another_pid_fails(tmp_path):
    lock_path = tmp_path / "gvm.lock"
    # Simulate a live holder: our own parent pid is definitely running.
    lock_path.write_text(f'pid = {os.getppid()}\nlabel = "gui"\n', encoding="utf-8")

    other = StateLock(lock_path, label="cli")
    assert other.held_by_other() is not None
    assert other.acquire() is False


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path):
    lock_path = tmp_path / "gvm.lock"
    # PID 2^22 is above the default pid_max on Linux and will not exist.
    lock_path.write_text('pid = 4194304\nlabel = "gui"\n', encoding="utf-8")

    lock = StateLock(lock_path, label="cli")
    assert lock.held_by_other() is None
    assert lock.acquire() is True
    lock.release()


def test_own_pid_does_not_block_itself(tmp_path):
    lock_path = tmp_path / "gvm.lock"
    lock_path.write_text(f'pid = {os.getpid()}\nlabel = "gui"\n', encoding="utf-8")
    assert StateLock(lock_path).held_by_other() is None


def test_release_does_not_remove_someone_elses_lock(tmp_path):
    lock_path = tmp_path / "gvm.lock"
    lock = StateLock(lock_path, label="cli")
    lock.acquire()
    # Another process takes over the file.
    lock_path.write_text(f'pid = {os.getppid()}\nlabel = "gui"\n', encoding="utf-8")
    lock.release()
    assert lock_path.exists(), "released a lock owned by another process"


def test_current_process_is_running():
    assert _pid_is_running(os.getpid()) is True


def test_missing_lock_file_reads_as_unlocked(tmp_path):
    assert StateLock(tmp_path / "absent.lock").read_owner() is None


# --- prefs rollback (H-4) ---------------------------------------------------

def test_atomic_write_replaces_contents(tmp_path):
    target = tmp_path / "prefs"
    target.write_bytes(b"old")
    _atomic_write_with_rollback(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_creates_a_new_file(tmp_path):
    target = tmp_path / "prefs"
    _atomic_write_with_rollback(target, b"fresh")
    assert target.read_bytes() == b"fresh"


def test_failed_write_leaves_original_intact(tmp_path, monkeypatch):
    """The whole point of H-4: a failed restore must not destroy the old prefs."""
    target = tmp_path / "prefs"
    target.write_bytes(b"precious user settings")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        _atomic_write_with_rollback(target, b"replacement")

    assert target.read_bytes() == b"precious user settings"


def test_failed_write_leaves_no_temp_files(tmp_path, monkeypatch):
    target = tmp_path / "prefs"
    target.write_bytes(b"old")
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        _atomic_write_with_rollback(target, b"new")
    assert [p.name for p in tmp_path.iterdir()] == ["prefs"]
