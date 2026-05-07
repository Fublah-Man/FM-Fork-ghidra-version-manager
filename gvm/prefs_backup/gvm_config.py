from dataclasses import dataclass

import tomllib
import tomli_w


@dataclass
class GvmConfig:
    version: int
    tag: str

    def to_toml_bytes(self) -> bytes:
        return tomli_w.dumps({"version": self.version, "tag": self.tag}).encode()

    @classmethod
    def from_toml_bytes(cls, data: bytes) -> "GvmConfig":
        d = tomllib.loads(data.decode())
        return cls(version=d["version"], tag=d["tag"])
