"""A tiny reader/writer for Ghidra's ``launch.properties`` file.

Ghidra's launch.properties uses ``KEY=value`` lines, and crucially the *same*
key (e.g. ``VMARGS_LINUX``) can appear multiple times to accumulate several
values. This helper preserves that "one key -> list of values" shape so GVM can
add/replace a single VM argument (the UI-scale override) without disturbing the
rest of the file.
"""

from pathlib import Path


class GhidraPropsFile:
    def __init__(self, fields: dict[str, list[str]]) -> None:
        # Maps each key to the list of values seen for it (in file order).
        self.fields = fields

    @classmethod
    def from_path(cls, path: Path) -> "GhidraPropsFile":
        """Parse a properties file from disk into a GhidraPropsFile."""
        fields: dict[str, list[str]] = {}
        # errors="replace" keeps a stray non-UTF-8 byte from aborting the parse.
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            # Skip comment lines.
            if line.startswith("#"):
                continue
            # Split on the first '=' only, so values may themselves contain '='.
            if "=" in line:
                eq = line.index("=")
                key = line[:eq]
                val = line[eq + 1:]
                # Append to this key's value list (creating it if new).
                fields.setdefault(key, []).append(val)
        # Store keys sorted for deterministic output.
        return cls(dict(sorted(fields.items())))

    def save_to_file(self, path: Path) -> None:
        """Serialise back to disk in ``KEY=value`` form."""
        path.write_text(self._generate_prop_content(), encoding="utf-8")

    def _generate_prop_content(self) -> str:
        # Emit every value of every key, one ``KEY=value`` line each, sorted for
        # stable, diff-friendly output.
        out = []
        for key, vals in sorted(self.fields.items()):
            for val in vals:
                out.append(f"{key}={val}\n")
        return "".join(out)

    def get_by_key(self, key: str) -> list[str] | None:
        # Return a *copy* of the value list (so callers can mutate freely), or
        # None if the key is absent.
        return list(self.fields[key]) if key in self.fields else None

    def put(self, key: str, vals: list[str]) -> None:
        # Replace all values for a key with the supplied list.
        self.fields[key] = vals
