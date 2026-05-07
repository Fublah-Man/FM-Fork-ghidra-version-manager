from pathlib import Path


class GhidraPropsFile:
    def __init__(self, fields: dict[str, list[str]]) -> None:
        self.fields = fields

    @classmethod
    def from_path(cls, path: Path) -> "GhidraPropsFile":
        fields: dict[str, list[str]] = {}
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            if "=" in line:
                eq = line.index("=")
                key = line[:eq]
                val = line[eq + 1:]
                fields.setdefault(key, []).append(val)
        return cls(dict(sorted(fields.items())))

    def save_to_file(self, path: Path) -> None:
        path.write_text(self._generate_prop_content(), encoding="utf-8")

    def _generate_prop_content(self) -> str:
        out = []
        for key, vals in sorted(self.fields.items()):
            for val in vals:
                out.append(f"{key}={val}\n")
        return "".join(out)

    def get_by_key(self, key: str) -> list[str] | None:
        return list(self.fields[key]) if key in self.fields else None

    def put(self, key: str, vals: list[str]) -> None:
        self.fields[key] = vals
