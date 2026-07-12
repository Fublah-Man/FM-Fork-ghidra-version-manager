"""Unit tests for prefs-backup metadata parsing and restore validation."""

import io
import zipfile

import pytest
import tomli_w

from gvm.cache import CacheEntry
from gvm.prefs_backup.backup_restorer import BackupRestorer
from gvm.prefs_backup.gvm_config import GvmConfig


class TestGvmConfig:
    def test_roundtrip(self):
        c = GvmConfig(version=0, tag="Ghidra_11.4")
        assert GvmConfig.from_toml_bytes(c.to_toml_bytes()) == c

    def test_missing_keys_default(self):
        c = GvmConfig.from_toml_bytes(tomli_w.dumps({}).encode())
        assert c.version == 0 and c.tag == ""

    def test_invalid_toml_raises_valueerror(self):
        with pytest.raises(ValueError):
            GvmConfig.from_toml_bytes(b"\x00not valid toml \xff")


class TestBackupRestorerValidation:
    def test_foreign_zip_raises_valueerror(self, tmp_path):
        z = tmp_path / "foreign.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("random.txt", "hello")
        with pytest.raises(ValueError):
            BackupRestorer.from_path(z).restore_to_cached_version(
                CacheEntry(path=str(tmp_path / "ghidra_x"))
            )

    def test_not_a_zip_raises_valueerror(self, tmp_path):
        # BackupRestorer takes raw bytes; a non-zip payload must fail cleanly.
        r = BackupRestorer(backup_data=b"not a zip at all")
        with pytest.raises(ValueError):
            r.restore_to_cached_version(CacheEntry(path=str(tmp_path / "ghidra_x")))

    def test_valid_backup_roundtrips_into_temp_home(self, tmp_path, monkeypatch):
        # A GVM backup with both members should restore without error. Redirect
        # HOME so we don't touch the real prefs dir.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("prefs", b"pref-bytes")
            zf.writestr("gvm_config.toml", GvmConfig(0, "t").to_toml_bytes())
        # Should not raise (writes into the redirected prefs path).
        BackupRestorer(backup_data=buf.getvalue()).restore_to_cached_version(
            CacheEntry(path=str(tmp_path / "ghidra_11.4_PUBLIC"))
        )
