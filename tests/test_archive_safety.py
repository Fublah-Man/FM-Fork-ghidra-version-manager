"""Hostile-archive tests for the extraction paths.

These encode the three cases that were run by hand during the audit, plus the
version-string validation added for C-3.
"""

import os
import sys
import zipfile

import pytest

from gvm.install import _check_download_host, _safe_extract_zip, _validate_version

# --- zip extraction ---------------------------------------------------------

def test_rejects_parent_traversal(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../pwned.txt", "x")

    with pytest.raises(RuntimeError, match="Unsafe path"):
        _safe_extract_zip(archive, dest)
    assert not (tmp_path.parent / "pwned.txt").exists()


def test_rejects_absolute_path(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    archive = tmp_path / "abs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/etc/pwned", "x")

    with pytest.raises(RuntimeError, match="absolute path"):
        _safe_extract_zip(archive, dest)


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks")
def test_rejects_escape_through_existing_symlink(tmp_path):
    """A symlink already inside dest must not become an escape hatch."""
    dest = tmp_path / "dest"
    dest.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(dest / "link"))

    archive = tmp_path / "sym.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("link/escaped.txt", "escaped!")

    with pytest.raises(RuntimeError, match="escapes target dir"):
        _safe_extract_zip(archive, dest)
    assert not (outside / "escaped.txt").exists()


def test_symlink_member_is_written_as_regular_file(tmp_path):
    """zipfile does not honour symlink members; confirm that stays true."""
    dest = tmp_path / "dest"
    dest.mkdir()
    archive = tmp_path / "symmember.zip"
    info = zipfile.ZipInfo("evil_link")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, "/etc/passwd")

    _safe_extract_zip(archive, dest)
    written = dest / "evil_link"
    assert not written.is_symlink()
    assert written.read_text() == "/etc/passwd"


def test_extracts_a_benign_archive(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ghidra_11.4_PUBLIC/support/launch.properties", "VMARGS=x")

    _safe_extract_zip(archive, dest)
    assert (dest / "ghidra_11.4_PUBLIC" / "support" / "launch.properties").is_file()


# --- version validation (C-3) -----------------------------------------------

@pytest.mark.parametrize("version", ["11.4", "10.1.2", "11", "11.4-DEV", "9.0_beta"])
def test_accepts_plausible_versions(version):
    assert _validate_version(version) == version


@pytest.mark.parametrize("version", [
    "../../../../tmp/evil",
    "/etc/cron.d/evil",
    "..",
    ".",
    "",
    "11.4; touch /tmp/pwned",
    "11.4$(id)",
    "a/b",
    "a\\b",
    "-leading-dash",
])
def test_rejects_unsafe_versions(version):
    with pytest.raises(RuntimeError, match="unsafe version"):
        _validate_version(version)


# --- download host allowlist ------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/x/y.zip",
    "https://objects.githubusercontent.com/foo",
    "https://codeload.github.com/a/b/tar.gz/main",
])
def test_allows_github_hosts(url):
    _check_download_host(url)


@pytest.mark.parametrize("url", [
    "https://evil.example.com/payload.zip",
    "http://github.com/a/b.zip",          # not HTTPS
    "https://github.com.evil.test/x.zip",  # suffix trick
])
def test_rejects_other_hosts(url):
    with pytest.raises(RuntimeError):
        _check_download_host(url)
