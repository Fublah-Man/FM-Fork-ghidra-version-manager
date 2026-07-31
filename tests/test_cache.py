"""Cache round-trip, atomic write and corruption-recovery tests (H-3)."""



from gvm.cache import Cache, CacheEntry, Cacher, ExtEntry, Prefs


def test_round_trip_preserves_everything(tmp_path):
    cache = Cache(
        entries={
            "Ghidra_11.4_build": CacheEntry(
                path="/opt/ghidra_11.4_PUBLIC",
                launcher="/home/u/.local/share/applications/ghidra_11.4.desktop",
                extensions={"findcrypt": ExtEntry(files=["/a/b"], tag="v1.2")},
            )
        },
        default="Ghidra_11.4_build",
        latest_known="Ghidra_11.4_build",
        prefs=Prefs(pyghidra=True, ui_scale_override=2, install_dir="/mnt/x"),
        last_launched="Ghidra_11.4_build",
    )
    path = tmp_path / "cache.toml"
    Cacher(cache, path).save()

    loaded = Cacher.load(path).cache
    assert loaded.default == "Ghidra_11.4_build"
    assert loaded.prefs.pyghidra is True
    assert loaded.prefs.ui_scale_override == 2
    assert loaded.prefs.install_dir == "/mnt/x"
    entry = loaded.entries["Ghidra_11.4_build"]
    assert entry.path == "/opt/ghidra_11.4_PUBLIC"
    assert entry.extensions["findcrypt"].tag == "v1.2"


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "cache.toml"
    cacher = Cacher(Cache(latest_known="Ghidra_11.4_build"), path)
    cacher.save()
    cacher.save()

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "cache.toml"]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_corrupt_cache_is_preserved_not_discarded(tmp_path):
    """A truncated cache must be moved aside, not silently overwritten."""
    path = tmp_path / "cache.toml"
    path.write_text('default = "Ghidra_11.4_build"\nlatest_kno', encoding="utf-8")

    cacher = Cacher.load(path)

    # Falls back to a clean cache so the CLI stays usable...
    assert cacher.cache.default == "latest"
    # ...but the damaged file survives for recovery.
    preserved = list(tmp_path.glob("cache.toml.corrupt-*"))
    assert len(preserved) == 1
    assert "latest_kno" in preserved[0].read_text(encoding="utf-8")


def test_saving_after_corruption_does_not_clobber_the_preserved_copy(tmp_path):
    path = tmp_path / "cache.toml"
    path.write_text("this is not toml {{{", encoding="utf-8")
    cacher = Cacher.load(path)
    cacher.save()

    preserved = list(tmp_path.glob("cache.toml.corrupt-*"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "this is not toml {{{"


def test_missing_cache_starts_clean(tmp_path):
    cacher = Cacher.load(tmp_path / "nope.toml")
    assert cacher.cache.entries == {}
    assert cacher.cache.default == "latest"


def test_malformed_extension_record_does_not_nuke_the_entry(tmp_path):
    path = tmp_path / "cache.toml"
    path.write_text(
        'default = "x"\n'
        "[entries.x]\n"
        'path = "/opt/x"\n'
        'extensions = { bad = "not-a-table" }\n',
        encoding="utf-8",
    )
    cache = Cacher.load(path).cache
    assert "x" in cache.entries
    assert cache.entries["x"].path == "/opt/x"


def test_hand_edited_string_scale_is_coerced(tmp_path):
    path = tmp_path / "cache.toml"
    path.write_text('[prefs]\nui_scale_override = "3"\n', encoding="utf-8")
    assert Cacher.load(path).cache.prefs.ui_scale_override == 3


def test_default_explicit_resolves_latest_sentinel():
    cacher = Cacher(Cache(default="latest", latest_known="Ghidra_11.4_build"), None)
    assert cacher.default_explicit() == "Ghidra_11.4_build"


def test_default_explicit_can_be_empty_before_any_update_check():
    cacher = Cacher(Cache(default="latest", latest_known=""), None)
    assert cacher.default_explicit() == ""
    assert cacher.is_installed("") is False
