"""Create a portable ZIP backup of a Ghidra version's preferences."""

import io
import zipfile
from pathlib import Path

from gvm.cache import CacheEntry
from gvm.prefs_backup import ghidra_prefs_path
from gvm.prefs_backup.gvm_config import GvmConfig


class BackupGenerator:
    def __init__(self, backup_data: bytes) -> None:
        # The finished ZIP archive, held in memory as raw bytes.
        self.backup_data = backup_data

    @classmethod
    def from_cached_version(cls, cache_entry: CacheEntry, tag: str) -> "BackupGenerator":
        """Build a backup from an installed version's preferences file.

        Raises ``FileNotFoundError`` (with a clear message) if the preferences
        file doesn't exist yet — which happens when the user has installed but
        never actually launched that version. Callers (CLI and GUI alike) catch
        this and report it as "no preferences to back up" rather than crashing.
        """
        # Ghidra's prefs dir is keyed by the install *folder* name, not the tag.
        install_dir = Path(cache_entry.path).name
        pref_path = ghidra_prefs_path(install_dir)

        # Fail early and descriptively rather than letting read_bytes raise a
        # bare FileNotFoundError with just a path.
        if not pref_path.exists():
            raise FileNotFoundError(
                f"No Ghidra preferences found at {pref_path}. "
                "Launch this version at least once before backing it up."
            )
        prefs_data = pref_path.read_bytes()

        # Pack the prefs blob plus a small GVM metadata file into a ZIP held in
        # memory (so callers decide where, if anywhere, to write it).
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("prefs", prefs_data)
            zf.writestr("gvm_config.toml", GvmConfig(version=0, tag=tag).to_toml_bytes())

        return cls(backup_data=buf.getvalue())

    def restorer(self) -> "BackupRestorer":
        """Return a restorer seeded with this backup's data (in-memory handoff)."""
        # Imported lazily to avoid a circular import between the two modules.
        from gvm.prefs_backup.backup_restorer import BackupRestorer
        return BackupRestorer(backup_data=self.backup_data)
