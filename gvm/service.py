"""Frontend-agnostic operations over GVM's state.

Both the CLI (:mod:`gvm.main`) and the GUI (:mod:`gvm.gui`) need to install a
version, remove one, resolve "which version does 'default' mean", locate the
runner script, and read or write preferences. Historically each frontend
implemented these separately, which is how they drifted — the GUI missed the
empty-tag guards the CLI grew, and the CLI's install-directory handling never
reached the GUI.

Everything here takes plain values and returns plain values or raises. No
argparse objects, no Tk widgets, no printing. That makes each operation usable
from either frontend and testable without either.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gvm.cache import Cacher
from gvm.install import install_version

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """An operation failed for a reason worth showing the user verbatim."""


@dataclass(frozen=True)
class InstallOptions:
    """The subset of CLI flags the install path actually consults.

    ``install_version`` reads these off an argparse namespace via ``getattr``,
    so this stands in for one without dragging argparse into the GUI.
    """

    offline: bool = False
    require_digest: bool = False
    use_cached: bool = False
    launcher: bool = False
    verbose: bool = False


def data_dir() -> Path:
    """GVM's per-user data directory (cache, lock, user extension registry)."""
    home = Path.home()
    if sys.platform == "win32":
        return home / "AppData" / "Local" / "gvm"
    return home / ".local" / "opt" / "gvm"


def install_dir(cacher: Cacher) -> Path:
    """Where Ghidra versions are installed, honouring the user's override."""
    configured = cacher.cache.prefs.install_dir
    return Path(configured) if configured else data_dir()


def resolve_tag(cacher: Cacher, tag: Optional[str], default: str = "default") -> str:
    """Resolve None / "default" / "latest" to a concrete tag.

    May return "" when "latest" or "default" point at ``latest_known`` and no
    update check has ever succeeded. Callers that turn the result into a URL or
    a cache key must check for that — :func:`require_tag` does it for you.
    """
    t = tag or default
    if t == "default":
        return cacher.default_explicit()
    if t == "latest":
        return cacher.cache.latest_known
    return t


def require_tag(cacher: Cacher, tag: Optional[str], action: str = "run") -> str:
    """Like :func:`resolve_tag`, but raise a clear error on the empty case.

    Every frontend needs this guard and each one used to write its own (or, in
    the GUI's case, not write one at all and hit a KeyError).
    """
    resolved = resolve_tag(cacher, tag)
    if not resolved:
        raise ServiceError(
            f"No version to {action}: none specified and the latest release "
            "isn't known yet. Install a version, or run a update check first."
        )
    return resolved


def installed_versions(cacher: Cacher) -> list[str]:
    """Tags of every installed version, newest-looking last."""
    return sorted(cacher.cache.entries.keys())


def is_installed(cacher: Cacher, tag: str) -> bool:
    return bool(tag) and tag in cacher.cache.entries


def runner_path(cacher: Cacher, tag: str, pyghidra: Optional[bool] = None) -> Path:
    """Absolute path to the launcher script for an installed version.

    *pyghidra* overrides the stored preference for a single launch.
    """
    entry = cacher.cache.entries.get(tag)
    if entry is None:
        raise ServiceError(f"Version {tag} is not installed")

    base = Path(entry.path)
    use_pyghidra = cacher.cache.prefs.pyghidra if pyghidra is None else pyghidra

    if use_pyghidra:
        name = "support/pyghidraRun.bat" if sys.platform == "win32" else "support/pyghidraRun"
    else:
        name = "ghidraRun.bat" if sys.platform == "win32" else "ghidraRun"
    return base / name


def check_runner(cacher: Cacher, tag: str, pyghidra: Optional[bool] = None) -> Path:
    """Return the runner path, distinguishing "moved away" from "broken".

    A missing *install directory* usually means an unmounted or relocated disk,
    so the cache record is kept. A present directory with no runner is a genuinely
    broken install, so the record is dropped.
    """
    runner = runner_path(cacher, tag, pyghidra)
    if runner.exists():
        return runner

    base = Path(cacher.cache.entries[tag].path)
    if not base.exists():
        raise ServiceError(
            f"Install directory not found: {base} (unmounted or moved?). "
            "Keeping the record — reconnect it, or uninstall to clear it."
        )

    del cacher.cache.entries[tag]
    cacher.save()
    raise ServiceError(
        f"Runner missing from {base}; the install looks broken — removed it"
    )


def launch(runner: Path, replace_process: bool = True) -> Optional[subprocess.Popen]:
    """Start Ghidra.

    When *replace_process* is true and the platform supports it, this replaces
    the current process and never returns — right for the CLI, wrong for the GUI,
    which passes False so its window survives the launch.
    """
    if replace_process and sys.platform == "linux":
        os.execv(str(runner), [str(runner)])
        raise AssertionError("unreachable")  # pragma: no cover

    if sys.platform == "win32":
        # .bat needs cmd; invoke it explicitly rather than via shell=True.
        return subprocess.Popen(["cmd", "/c", str(runner)])
    return subprocess.Popen([str(runner)])


def install(cacher: Cacher, tag: str, options: Optional[InstallOptions] = None) -> None:
    """Install *tag*, raising ServiceError instead of just logging on failure."""
    opts = options or InstallOptions()
    target = install_dir(cacher)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ServiceError(f"Install directory {target} is unavailable: {e}") from e

    install_version(cacher, opts, target, tag)

    if tag not in cacher.cache.entries:
        raise ServiceError(f"Install of {tag} did not complete")


def uninstall(cacher: Cacher, tag: str) -> None:
    """Remove an installed version, its launcher, and its cache record."""
    import shutil

    entry = cacher.cache.entries.get(tag)
    if entry is None:
        raise ServiceError(f"{tag} isn't installed")

    shutil.rmtree(entry.path, ignore_errors=True)
    if entry.launcher:
        launcher = Path(entry.launcher)
        if launcher.is_dir():
            shutil.rmtree(launcher, ignore_errors=True)
        elif launcher.exists():
            launcher.unlink()

    del cacher.cache.entries[tag]
    cacher.save()


# --- preferences ------------------------------------------------------------

MIN_UI_SCALE = 1
MAX_UI_SCALE = 16


def set_ui_scale(cacher: Cacher, value) -> int:
    """Validate and store the UI scale override."""
    try:
        scale = int(value)
    except (TypeError, ValueError):
        raise ServiceError(f"UI scale must be an integer, got: {value}") from None
    if not MIN_UI_SCALE <= scale <= MAX_UI_SCALE:
        raise ServiceError(
            f"UI scale must be between {MIN_UI_SCALE} and {MAX_UI_SCALE}, got: {scale}"
        )
    cacher.cache.prefs.ui_scale_override = scale
    cacher.save()
    return scale


def set_pyghidra(cacher: Cacher, enabled: bool) -> None:
    cacher.cache.prefs.pyghidra = bool(enabled)
    cacher.save()


def set_install_dir(cacher: Cacher, value: Optional[str]) -> str:
    """Set (or reset, with "default"/None) the install directory."""
    if not value or value == "default":
        cacher.cache.prefs.install_dir = ""
        cacher.save()
        return ""

    path = Path(value).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ServiceError(f"Can't use {path} as the install directory: {e}") from e
    cacher.cache.prefs.install_dir = str(path)
    cacher.save()
    return str(path)


def set_ext_dir(cacher: Cacher, value: Optional[str]) -> str:
    """Set (or clear, with "default"/None) the local extensions directory."""
    if not value or value == "default":
        cacher.cache.prefs.ext_dir = ""
        cacher.save()
        return ""

    path = Path(value).expanduser()
    if not path.is_dir():
        raise ServiceError(f"Not a directory: {path}")
    cacher.cache.prefs.ext_dir = str(path)
    cacher.save()
    return str(path)
