"""Regression tests for C-1 and C-2 — the packaged data files.

`extensions-repo/` and `res/` used to live at the repo root and were absent from
every non-editable install: the registry silently loaded 0 of its 27 entries and
macOS installs died on a missing plist template *after* extracting Ghidra.

These tests fail if the data ever stops shipping inside the package.
"""

from pathlib import Path

import pytest

import gvm.extensions as extensions
import gvm.install as install


def test_bundled_registry_directory_exists():
    assert extensions.EXTENSIONS_REPO.is_dir(), (
        f"Bundled registry missing at {extensions.EXTENSIONS_REPO}. "
        "Is extensions-repo/ still declared in pyproject package-data?"
    )


def test_bundled_registry_is_inside_the_package():
    """It must sit under gvm/, not one level up at the repo root."""
    pkg_dir = Path(extensions.__file__).parent
    assert extensions.EXTENSIONS_REPO.resolve().is_relative_to(pkg_dir.resolve())


def test_registry_loads_all_bundled_extensions():
    entries = extensions._load_all_extensions()
    assert len(entries) >= 27, f"expected >=27 registry entries, got {len(entries)}"


def test_every_registry_entry_has_required_fields():
    for ext in extensions._load_all_extensions():
        assert ext.get("name"), f"registry entry missing 'name': {ext}"
        assert ext.get("slug"), f"registry entry missing 'slug': {ext}"
        assert ext.get("kind") in {"DownloadOnly", "ProcessorGit", "Local"}, (
            f"{ext.get('name')} has unknown kind {ext.get('kind')!r}"
        )


def test_registry_slugs_are_unique():
    slugs = [e["slug"] for e in extensions._load_all_extensions()]
    assert len(slugs) == len(set(slugs)), "duplicate slugs in the bundled registry"


def test_macos_plist_template_is_readable():
    """C-2: this read is unguarded in install_version, mid-install."""
    content = install._read_package_resource("res/macos_plist.plist")
    assert "{name}" in content and "{version}" in content


def test_find_by_name_resolves_a_known_extension():
    ext = extensions.find_by_name("FindCrypt")
    assert ext["slug"]


def test_find_by_name_raises_for_unknown():
    with pytest.raises(ValueError):
        extensions.find_by_name("definitely-not-an-extension")
