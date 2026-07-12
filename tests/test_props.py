"""Unit tests for the launch.properties reader/writer."""

from gvm.ghidra_props_parser import GhidraPropsFile


class TestGhidraPropsFile:
    def test_key_whitespace_stripped(self, tmp_path):
        f = tmp_path / "launch.properties"
        f.write_text("# comment\n\n  VMARGS_LINUX = -Xmx2G\nMAXMEM=2G\n")
        gp = GhidraPropsFile.from_path(f)
        # The key is matched despite surrounding whitespace.
        assert gp.get_by_key("VMARGS_LINUX") == [" -Xmx2G"]
        assert gp.get_by_key("MAXMEM") == ["2G"]

    def test_duplicate_keys_accumulate(self, tmp_path):
        f = tmp_path / "launch.properties"
        f.write_text("VMARGS_LINUX=-Xmx2G\nVMARGS_LINUX=-Xms1G\n")
        gp = GhidraPropsFile.from_path(f)
        assert gp.get_by_key("VMARGS_LINUX") == ["-Xmx2G", "-Xms1G"]

    def test_value_with_equals_preserved(self, tmp_path):
        f = tmp_path / "launch.properties"
        f.write_text("VMARGS_LINUX=-Dfoo=bar\n")
        gp = GhidraPropsFile.from_path(f)
        assert gp.get_by_key("VMARGS_LINUX") == ["-Dfoo=bar"]

    def test_roundtrip(self, tmp_path):
        f = tmp_path / "launch.properties"
        f.write_text("KEY_B=2\nKEY_A=1\nKEY_A=3\n")
        gp = GhidraPropsFile.from_path(f)
        out = tmp_path / "out.properties"
        gp.save_to_file(out)
        gp2 = GhidraPropsFile.from_path(out)
        assert gp2.get_by_key("KEY_A") == ["1", "3"]
        assert gp2.get_by_key("KEY_B") == ["2"]

    def test_put_replaces(self, tmp_path):
        f = tmp_path / "launch.properties"
        f.write_text("K=old\n")
        gp = GhidraPropsFile.from_path(f)
        gp.put("K", ["new"])
        assert gp.get_by_key("K") == ["new"]
