import io
import logging
import zipfile
from pathlib import Path

from gvm.cache import CacheEntry
from gvm.prefs_backup import ghidra_prefs_path
from gvm.prefs_backup.gvm_config import GvmConfig

logger = logging.getLogger(__name__)


class BackupRestorer:
    def __init__(self, backup_data: bytes) -> None:
        self.backup_data = backup_data

    @classmethod
    def from_path(cls, p: Path) -> "BackupRestorer":
        return cls(backup_data=p.read_bytes())

    def restore_to_cached_version(self, cache_entry: CacheEntry) -> None:
        install_dir = Path(cache_entry.path).name
        pref_path = ghidra_prefs_path(install_dir)

        with zipfile.ZipFile(io.BytesIO(self.backup_data), "r") as zf:
            prefs_data = zf.read("prefs")
            cfg = GvmConfig.from_toml_bytes(zf.read("gvm_config.toml"))

        logger.info("Restoring backup version %d from %s", cfg.version, cfg.tag)
        pref_path.parent.mkdir(parents=True, exist_ok=True)
        pref_path.write_bytes(prefs_data)
