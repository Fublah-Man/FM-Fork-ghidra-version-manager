"""Small metadata record embedded inside each preferences backup ZIP.

Storing a version number and the source tag alongside the raw preferences lets
future GVM versions recognise (and if necessary migrate) older backup formats.
"""

from dataclasses import dataclass

import tomllib  # TOML reader (stdlib, 3.11+)
import tomli_w  # TOML writer (third-party)


@dataclass
class GvmConfig:
    # Backup format version (currently always 0).
    version: int
    # The Ghidra release tag the backup was taken from.
    tag: str

    def to_toml_bytes(self) -> bytes:
        """Serialise to UTF-8 TOML bytes for storage inside the ZIP."""
        return tomli_w.dumps({"version": self.version, "tag": self.tag}).encode()

    @classmethod
    def from_toml_bytes(cls, data: bytes) -> "GvmConfig":
        """Parse the metadata back from the TOML bytes stored in a backup."""
        d = tomllib.loads(data.decode())
        return cls(version=d["version"], tag=d["tag"])
