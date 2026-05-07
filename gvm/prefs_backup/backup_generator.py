import io
import zipfile
from pathlib import Path

from gvm.cache import CacheEntry
from gvm.prefs_backup.gvm_config import GvmConfig


class BackupGenerator:
    def __init__(self, backup_data: bytes) -> None:
        self.backup_data = backup_data

    @classmethod
    def from_cached_version(cls, cache_entry: CacheEntry, tag: str) -> "BackupGenerator":
        install_dir = Path(cache_entry.path).name
        pref_path = Path.home() / ".config" / "ghidra" / install_dir / "preferences"
        prefs_data = pref_path.read_bytes()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("prefs", prefs_data)
            zf.writestr("gvm_config.toml", GvmConfig(version=0, tag=tag).to_toml_bytes())

        return cls(backup_data=buf.getvalue())

    def restorer(self) -> "BackupRestorer":
        from gvm.prefs_backup.backup_restorer import BackupRestorer
        return BackupRestorer(backup_data=self.backup_data)
