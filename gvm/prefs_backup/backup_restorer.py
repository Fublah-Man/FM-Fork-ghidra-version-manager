"""Restore a Ghidra preferences ZIP produced by :mod:`backup_generator`."""

import io
import logging
import os
import tempfile
import zipfile
from pathlib import Path

from gvm.cache import CacheEntry
from gvm.prefs_backup import ghidra_prefs_path
from gvm.prefs_backup.gvm_config import GvmConfig

logger = logging.getLogger(__name__)


def _atomic_write_with_rollback(target: Path, data: bytes) -> None:
    """Replace *target* with *data*, restoring the original on any failure.

    Restoring preferences overwrites a file the user may have spent a long time
    customising. A plain ``write_bytes`` truncates the destination immediately,
    so an interrupted write destroyed the old preferences with no way back.

    Two protections: the new bytes go to a temp file in the same directory and
    are moved into place with ``os.replace`` (atomic — readers see either the
    whole old file or the whole new one), and the previous contents are kept in
    memory so a failure at any point can put them back.
    """
    # Snapshot whatever is currently there so we have something to roll back to.
    previous: bytes | None = None
    if target.exists():
        try:
            previous = target.read_bytes()
        except OSError as e:
            raise OSError(
                f"Refusing to overwrite {target}: couldn't read the existing "
                f"preferences to make a rollback copy ({e})"
            ) from e

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        _rollback(target, previous)
        raise


def _rollback(target: Path, previous: bytes | None) -> None:
    """Put *previous* back at *target* if the file was disturbed.

    ``os.replace`` is atomic, so in practice the destination is either fully
    replaced or completely untouched — this is belt-and-braces for the case
    where something interferes between the snapshot and the replace.
    """
    if previous is None:
        # There was no file before; the safest end state is still no file.
        target.unlink(missing_ok=True)
        return
    try:
        current = target.read_bytes() if target.exists() else None
        if current == previous:
            return  # Untouched, nothing to undo.
        target.write_bytes(previous)
        logger.warning("Restore failed; rolled %s back to its previous contents", target)
    except OSError as e:
        logger.error("Restore failed AND rollback failed for %s: %s", target, e)


class BackupRestorer:
    def __init__(self, backup_data: bytes) -> None:
        # The backup ZIP as raw bytes (either read from disk or handed over in
        # memory by a BackupGenerator).
        self.backup_data = backup_data

    @classmethod
    def from_path(cls, p: Path) -> "BackupRestorer":
        """Load a backup ZIP from a file on disk."""
        return cls(backup_data=p.read_bytes())

    def restore_to_cached_version(self, cache_entry: CacheEntry) -> None:
        """Write the backed-up preferences into *cache_entry*'s prefs location."""
        # Target the prefs path for this version's install folder.
        install_dir = Path(cache_entry.path).name
        pref_path = ghidra_prefs_path(install_dir)

        # Pull the prefs blob and the GVM metadata back out of the ZIP, refusing
        # anything that isn't a GVM backup rather than crashing with a KeyError.
        try:
            with zipfile.ZipFile(io.BytesIO(self.backup_data), "r") as zf:
                names = set(zf.namelist())
                if not {"prefs", "gvm_config.toml"} <= names:
                    raise ValueError(
                        "Not a GVM backup (missing prefs/gvm_config.toml)"
                    )
                prefs_data = zf.read("prefs")
                cfg = GvmConfig.from_toml_bytes(zf.read("gvm_config.toml"))
        except zipfile.BadZipFile as e:
            raise ValueError(f"Not a valid backup ZIP: {e}") from e

        logger.info("Restoring backup version %d from %s", cfg.version, cfg.tag)
        # Ensure the (possibly brand-new) config directory exists, then write.
        pref_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_with_rollback(pref_path, prefs_data)
