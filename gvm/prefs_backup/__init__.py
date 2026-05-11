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
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".config"
    return base / "ghidra" / install_dir_name / "preferences"
