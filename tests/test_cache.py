"""Unit tests for gvm.cache (de)serialization and tolerance."""

from gvm.cache import Cache, CacheEntry, ExtEntry, Prefs


class TestPrefs:
    def test_scale_coercion_from_string(self):
        assert Prefs.from_dict({"ui_scale_override": "2"}).ui_scale_override == 2

    def test_scale_coercion_from_junk_defaults(self):
        assert Prefs.from_dict({"ui_scale_override": "junk"}).ui_scale_override == 1

    def test_bool_coercion(self):
        p = Prefs.from_dict({"pyghidra": 1, "keep_gui_open": 0})
        assert p.pyghidra is True and p.keep_gui_open is False

    def test_defaults(self):
        p = Prefs.from_dict({})
        assert p.keep_gui_open is True and p.ui_scale_override == 1

    def test_roundtrip(self):
        p = Prefs(pyghidra=True, ui_scale_override=3, ext_dir="/x", keep_gui_open=False)
        assert Prefs.from_dict(p.to_dict()) == p


class TestExtEntry:
    def test_tag_roundtrip(self):
        e = ExtEntry(files=["/a"], tag="v1.2")
        assert e.to_dict() == {"files": ["/a"], "tag": "v1.2"}
        assert ExtEntry.from_dict(e.to_dict()).tag == "v1.2"

    def test_tag_absent_defaults_empty(self):
        assert ExtEntry.from_dict({"files": ["/a"]}).tag == ""


class TestCacheEntryTolerance:
    def test_malformed_sub_record_skipped(self):
        ce = CacheEntry.from_dict(
            {"path": "/x", "extensions": {"good": {"files": []}, "bad": "notadict"}}
        )
        assert "good" in ce.extensions
        assert "bad" not in ce.extensions

    def test_roundtrip(self):
        ce = CacheEntry(path="/x", extensions={"s": ExtEntry(files=["/f"], tag="t")})
        back = CacheEntry.from_dict(ce.to_dict())
        assert back.path == "/x"
        assert back.extensions["s"].tag == "t"


class TestCacheRoundtrip:
    def test_roundtrip(self):
        c = Cache()
        c.entries["11.0"] = CacheEntry(path="/x")
        c.default = "11.0"
        c.latest_known = "11.0"
        back = Cache.from_dict(c.to_dict())
        assert back.default == "11.0"
        assert "11.0" in back.entries
