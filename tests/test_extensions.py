"""Unit tests for gvm.extensions pure logic (no network)."""

import zipfile

import pytest

from gvm.extensions import (
    _generate_slug,
    _safe_child,
    _scan_ext_dir,
    _select_asset,
    install_local_source,
    parse_git_url,
)


class TestParseGitUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "http://github.com/owner/repo/",
            "git@github.com:owner/repo.git",
            "github.com/owner/repo",
            "owner/repo",
            "https://github.com/owner/repo/tree/main",
        ],
    )
    def test_valid_forms(self, url):
        assert parse_git_url(url) == ("owner", "repo")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "justonesegment",
            "https://github.com/owner",
            r"git@github.com:owner/re\..\..\evil",
            "https://github.com/ow ner/repo",
            "https://github.com/owner/re;po",
        ],
    )
    def test_rejects_bad(self, bad):
        with pytest.raises(ValueError):
            parse_git_url(bad)


class TestSafeChild:
    def test_plain_name(self, tmp_path):
        assert _safe_child(tmp_path, "ext.zip") == (tmp_path / "ext.zip").resolve()

    @pytest.mark.parametrize(
        "attack",
        ["../../etc/passwd", r"..\..\evil", "/abs/evil", "../../../.bashrc"],
    )
    def test_traversal_contained_to_basename(self, tmp_path, attack):
        got = _safe_child(tmp_path, attack)
        assert got.parent == tmp_path.resolve()

    @pytest.mark.parametrize("bad", ["", ".", "..", "foo/../.."])
    def test_rejects_empty_or_dot(self, tmp_path, bad):
        with pytest.raises(ValueError):
            _safe_child(tmp_path, bad)


class TestSelectAsset:
    def test_pattern_match(self):
        assets = [{"name": "source.zip"}, {"name": "ghidra_11_PUBLIC_x_nanomips.zip"}]
        assert _select_asset(assets, "ghidra_*_nanomips.zip")["name"].endswith("nanomips.zip")

    def test_no_pattern_takes_first(self):
        assets = [{"name": "a.zip"}, {"name": "b.zip"}]
        assert _select_asset(assets, "")["name"] == "a.zip"

    def test_no_match_falls_back_to_first(self):
        assets = [{"name": "a.zip"}, {"name": "b.zip"}]
        assert _select_asset(assets, "*.tar.gz")["name"] == "a.zip"

    def test_empty_raises(self):
        with pytest.raises(RuntimeError):
            _select_asset([], "")


def _make_ext_zip(path, root="MyExt", version="1.0", created="2025-01-01"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"{root}/extension.properties",
            f"name={root}\nversion={version}\ncreatedOn={created}\n",
        )
        zf.writestr(f"{root}/lib/x.jar", "x")


class TestInstallLocalSource:
    def test_unpacks_zip(self, tmp_path):
        z = tmp_path / "MyExt-1.0.zip"
        _make_ext_zip(z)
        dest = tmp_path / "install" / "Ghidra" / "Extensions"
        root = install_local_source(z, dest)
        assert root == "MyExt"
        assert (dest / "MyExt" / "extension.properties").is_file()

    def test_duplicate_raises(self, tmp_path):
        z = tmp_path / "MyExt-1.0.zip"
        _make_ext_zip(z)
        dest = tmp_path / "install" / "Ghidra" / "Extensions"
        install_local_source(z, dest)
        with pytest.raises(FileExistsError):
            install_local_source(z, dest, overwrite=False)

    def test_overwrite_replaces(self, tmp_path):
        z = tmp_path / "MyExt-1.0.zip"
        _make_ext_zip(z)
        dest = tmp_path / "install" / "Ghidra" / "Extensions"
        install_local_source(z, dest)
        # Should not raise.
        assert install_local_source(z, dest, overwrite=True) == "MyExt"

    def test_directory_source(self, tmp_path):
        src = tmp_path / "SomeExt"
        (src / "lib").mkdir(parents=True)
        (src / "extension.properties").write_text("name=SomeExt\n")
        dest = tmp_path / "install" / "Ghidra" / "Extensions"
        root = install_local_source(src, dest)
        assert root == "SomeExt"
        assert (dest / "SomeExt" / "extension.properties").is_file()


class TestScanExtDir:
    def test_finds_dir_and_zip(self, tmp_path):
        # A directory-style extension.
        d = tmp_path / "DirExt"
        d.mkdir()
        (d / "extension.properties").write_text(
            "name=DirExt\nversion=2.0\ncreatedOn=2024-06-01\n"
        )
        # A zip-style extension.
        _make_ext_zip(tmp_path / "ZipExt.zip", root="ZipExt", version="3.0")

        found = {e["name"]: e for e in _scan_ext_dir(tmp_path)}
        assert "DirExt" in found and found["DirExt"]["version"] == "2.0"
        assert found["DirExt"]["createdOn"] == "2024-06-01"
        assert "ZipExt" in found and found["ZipExt"]["version"] == "3.0"


class TestGenerateSlug:
    def test_normalizes(self):
        assert _generate_slug("My Cool_Ext", "directory") == "local-my-cool-ext"

    def test_strips_specials(self):
        assert _generate_slug("A/B:C", "zip").startswith("local-")
