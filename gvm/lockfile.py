"""Cross-process lock guarding GVM's mutable state.

``cache.toml`` is read-modify-write: a command loads the whole cache, mutates an
in-memory copy, and writes it all back. Two GVM processes doing that at once are
last-writer-wins, so a `gvm extensions install` running while the GUI has the
cache loaded can have its record silently erased when the GUI next saves.

The GUI holds this lock for its whole session and the CLI refuses to run
mutating commands while it is held, which is the behaviour we want: the GUI is
long-lived and interactive, so it owns the state while it is open.

The lock is a small TOML-ish file containing the owning PID and a label. It is
created with ``O_CREAT | O_EXCL`` so acquisition is atomic — no check-then-write
race. A lock whose recorded PID is no longer running is treated as stale and
reclaimed, which covers the case of a crashed or force-killed process.
"""

import errno
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

LOCK_FILENAME = "gvm.lock"


def _pid_is_running(pid: int) -> bool:
    """Return True if a process with *pid* currently exists.

    Deliberately conservative: when we cannot tell, we report True so a live
    lock is never stolen from a running process.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # No os.kill(pid, 0) on Windows; ask the OS for a handle instead.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still running.
        return True
    return True


class StateLock:
    """Advisory lock over GVM's cache directory."""

    def __init__(self, lock_path: Path, label: str = "gvm") -> None:
        self.lock_path = lock_path
        self.label = label
        self._acquired = False

    # -- inspection ---------------------------------------------------------

    def read_owner(self) -> tuple[int, str] | None:
        """Return ``(pid, label)`` of the current holder, or None if unlocked."""
        try:
            raw = self.lock_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        pid, label = 0, "unknown"
        for line in raw.splitlines():
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"')
            if key == "pid":
                try:
                    pid = int(value)
                except ValueError:
                    pid = 0
            elif key == "label":
                label = value or "unknown"
        return (pid, label)

    def held_by_other(self) -> tuple[int, str] | None:
        """Return the live holder if someone *else* holds the lock, else None.

        Clears the lock file if it refers to a process that no longer exists.
        """
        owner = self.read_owner()
        if owner is None:
            return None
        pid, label = owner
        if pid == os.getpid():
            return None
        if not _pid_is_running(pid):
            logger.debug("Clearing stale lock from dead pid %s", pid)
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return owner

    # -- acquisition --------------------------------------------------------

    def acquire(self) -> bool:
        """Try to take the lock. Returns True on success, False if held."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                # Someone holds it — or did. held_by_other() clears a stale
                # lock, in which case the retry below succeeds.
                if self.held_by_other() is not None:
                    return False
                continue
            except OSError as e:
                if e.errno == errno.EACCES:
                    logger.debug("No permission to create lock at %s", self.lock_path)
                    return False
                raise
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f'pid = {os.getpid()}\nlabel = "{self.label}"\n')
            self._acquired = True
            return True
        return False

    def release(self) -> None:
        """Release the lock if this process holds it."""
        if not self._acquired:
            return
        owner = self.read_owner()
        # Only remove a lock that is still ours — never clear someone else's.
        if owner is not None and owner[0] == os.getpid():
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Couldn't release lock: %s", e)
        self._acquired = False

    def __enter__(self) -> "StateLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def state_lock(data_dir: Path, label: str = "gvm") -> StateLock:
    """Build the StateLock for a GVM data directory."""
    return StateLock(data_dir / LOCK_FILENAME, label=label)
