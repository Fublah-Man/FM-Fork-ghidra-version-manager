"""Unit tests for gvm.install helpers (no network)."""

import zipfile

import pytest

from gvm.install import _safe_extract_zip, apply_ui_scale


def _make_install(tmp_path, props_text):
    d = tmp_path / "ghidra_x_PUBLIC"
    (d / "support").mkdir(parents=True)
    (d / "support" / "launch.properties").write_text(props_text)
    return d


class TestApplyUiScale:
    def test_patches_all_vmargs_keys(self, tmp_path):
        d = _make_install(
            tmp_path, "VMARGS_LINUX=-Xmx2G\nVMARGS_WIN=-Xmx2G\nVMARGS_MACOS=-Xmx2G\n"
        )
        apply_ui_scale(d, 3)
        out = (d / "support" / "launch.properties").read_text()
        assert out.count("-Dsun.java2d.uiScale=3") == 3

    def test_idempotent(self, tmp_path):
        d = _make_install(tmp_path, "VMARGS_LINUX=-Xmx2G\n")
        apply_ui_scale(d, 2)
        apply_ui_scale(d, 4)
        out = (d / "support" / "launch.properties").read_text()
        assert out.count("uiScale") == 1
        assert "uiScale=4" in out

    def test_missing_vmargs_does_not_raise(self, tmp_path):
        d = _make_install(tmp_path, "MAXMEM=2G\n")
        # Should warn and return, not raise.
        apply_ui_scale(d, 2)
        assert "uiScale" not in (d / "support" / "launch.properties").read_text()


class TestSafeExtractZip:
    def test_extracts_normal_zip(self, tmp_path):
        z = tmp_path / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("root/file.txt", "hi")
        dest = tmp_path / "out"
        dest.mkdir()
        _safe_extract_zip(z, dest)
        assert (dest / "root" / "file.txt").read_text() == "hi"

    def test_rejects_traversal(self, tmp_path):
        z = tmp_path / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../escape.txt", "x")
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(Exception):
            _safe_extract_zip(z, dest)
        assert not (tmp_path / "escape.txt").exists()
