"""Tests for the core-robustness changes (atomic save, extraction, headers)."""

import os
import zipfile

from gvm.cache import Cache, CacheEntry, Cacher
from gvm.extensions import scan_installed_extensions
from gvm.http_util import gh_headers
from gvm.install import _safe_extract_zip


class TestGhHeaders:
    def test_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert gh_headers() == {"User-Agent": "gvm"}

    def test_with_github_token(self, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "abc")
        assert gh_headers()["Authorization"] == "Bearer abc"

    def test_gh_token_fallback(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "xyz")
        assert gh_headers()["Authorization"] == "Bearer xyz"

    def test_extra_merged(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        assert gh_headers({"Accept": "application/json"})["Accept"] == "application/json"


class TestSafeExtractReturnsTopLevel:
    def test_single_top_level(self, tmp_path):
        z = tmp_path / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("ghidra_11.4_PUBLIC/support/x", "y")
            zf.writestr("ghidra_11.4_PUBLIC/ghidraRun", "z")
        out = tmp_path / "out"
        out.mkdir()
        assert _safe_extract_zip(z, out) == "ghidra_11.4_PUBLIC"

    def test_multiple_top_level_returns_none(self, tmp_path):
        z = tmp_path / "a.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a/x", "1")
            zf.writestr("b/y", "2")
        out = tmp_path / "out"
        out.mkdir()
        assert _safe_extract_zip(z, out) is None


class TestAtomicSave:
    def test_save_and_reload(self, tmp_path):
        cp = tmp_path / "cache.toml"
        c = Cacher(Cache(), cp)
        c.cache.default = "latest"
        c.cache.entries["11.0"] = CacheEntry(path="/x")
        c.save()
        assert Cacher.load(cp).cache.default == "latest"

    def test_no_temp_file_left(self, tmp_path):
        cp = tmp_path / "cache.toml"
        Cacher(Cache(), cp).save()
        assert not list(tmp_path.glob(".cache.*.tmp"))

    def test_creates_parent_dir(self, tmp_path):
        cp = tmp_path / "sub" / "dir" / "cache.toml"
        Cacher(Cache(), cp).save()
        assert cp.exists()


class TestDualPathScan:
    def test_scans_nested_ghidra_layout(self, tmp_path):
        # Nested layout: <install>/Ghidra/Extensions/Ghidra/<Ext>/
        nested = tmp_path / "Ghidra" / "Extensions" / "Ghidra" / "NestedExt"
        nested.mkdir(parents=True)
        (nested / "extension.properties").write_text("name=NestedExt\n")
        names = {e["name"] for e in scan_installed_extensions(tmp_path)}
        assert "NestedExt" in names

    def test_scans_flat_layout(self, tmp_path):
        flat = tmp_path / "Ghidra" / "Extensions" / "FlatExt"
        flat.mkdir(parents=True)
        (flat / "extension.properties").write_text("name=FlatExt\n")
        names = {e["name"] for e in scan_installed_extensions(tmp_path)}
        assert "FlatExt" in names
