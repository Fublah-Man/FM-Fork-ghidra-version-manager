"""Helpers for locating and backing up Ghidra's user preferences.

Ghidra stores per-version user preferences (key bindings, recent projects, ...)
outside the install directory, in a platform-specific config location. This
package can snapshot that preferences file into a portable ZIP and restore it
again — used both for explicit `gvm settings backup/restore` and for the
automatic prefs migration when switching versions.
"""

import sys
from pathlib import Path


def ghidra_prefs_path(install_dir_name: str) -> Path:
    """Return the platform-specific path to Ghidra's preferences file.

    Args:
        install_dir_name: The Ghidra installation folder name,
            e.g. ``ghidra_11.4_PUBLIC``.

    Returns:
        Path to the ``preferences`` file for that version.

    Platform paths:
        Windows: %APPDATA%/ghidra/<install_dir>/preferences
        Linux/macOS: ~/.config/ghidra/<install_dir>/preferences
    """
    # Ghidra keys its config directory off the install folder name, so the
    # caller passes that (not the version tag).
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".config"
    return base / "ghidra" / install_dir_name / "preferences"
