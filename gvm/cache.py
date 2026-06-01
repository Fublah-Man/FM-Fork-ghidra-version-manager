"""Persistent state for GVM.

Everything GVM needs to remember between runs lives in a single TOML file
(``cache.toml``): which Ghidra versions are installed, where they live on
disk, which extensions each version has, the user's preferences, and some
bookkeeping (the last-known latest release, when we last checked for updates,
and which version was launched most recently).

The module is organised as a set of small ``@dataclass`` "records" that each
know how to convert themselves to/from a plain ``dict`` (which is what the
``tomllib``/``tomli_w`` libraries read and write), plus a ``Cacher`` wrapper
that handles loading the file, saving it, and a couple of convenience lookups.
"""

import logging
import tomllib  # standard-library TOML *reader* (Python 3.11+)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tomli_w  # third-party TOML *writer* (stdlib has no writer)

# Module-level logger; messages are routed through the config set up in main().
logger = logging.getLogger(__name__)


@dataclass
class ExtEntry:
    """One installed extension, recorded as the list of files/dirs it created.

    We only track the paths so that uninstalling an extension can delete
    exactly what was added without guessing.
    """

    # Absolute paths of every file (or directory) this extension installed.
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        # Serialise to the shape stored in the TOML file.
        return {"files": self.files}

    @classmethod
    def from_dict(cls, d: dict) -> "ExtEntry":
        # Rebuild from TOML data; tolerate a missing "files" key (old caches).
        return cls(files=d.get("files", []))


@dataclass
class CacheEntry:
    """One installed Ghidra version."""

    # Absolute path to the unpacked Ghidra directory (e.g. .../ghidra_11.4_PUBLIC).
    path: str = ""
    # Path to the desktop launcher we created (.desktop file / .app bundle).
    # None on platforms where we don't create one (e.g. Windows).
    launcher: Optional[str] = None
    # Map of extension slug -> ExtEntry for everything installed into this version.
    extensions: dict[str, ExtEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        # Always write the path; only write optional keys when they have content
        # so the TOML file stays tidy.
        d: dict = {"path": self.path}
        if self.launcher is not None:
            d["launcher"] = self.launcher
        if self.extensions:
            d["extensions"] = {k: v.to_dict() for k, v in self.extensions.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CacheEntry":
        # Reconstruct nested ExtEntry objects from the raw dicts.
        exts = {k: ExtEntry.from_dict(v) for k, v in d.get("extensions", {}).items()}
        return cls(
            path=d.get("path", ""),
            launcher=d.get("launcher"),
            extensions=exts,
        )


@dataclass
class Prefs:
    """User-configurable preferences."""

    # When True, launchers use PyGhidra (pyghidraRun) instead of plain ghidraRun.
    pyghidra: bool = False
    # Java2D UI scale factor written into launch.properties (1 = no override).
    ui_scale_override: int = 1
    # Custom install directory. Empty string means "use the default location".
    install_dir: str = ""
    # Directory scanned for locally-supplied extensions. Empty means "not set".
    ext_dir: str = ""

    def to_dict(self) -> dict:
        # Always persist the two scalar settings; only persist the directory
        # overrides when set so an unset value round-trips as "" not "<cwd>".
        d: dict = {"pyghidra": self.pyghidra, "ui_scale_override": self.ui_scale_override}
        if self.install_dir:
            d["install_dir"] = self.install_dir
        if self.ext_dir:
            d["ext_dir"] = self.ext_dir
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Prefs":
        # Each field falls back to its default if absent from the TOML.
        return cls(
            pyghidra=d.get("pyghidra", False),
            ui_scale_override=d.get("ui_scale_override", 1),
            install_dir=d.get("install_dir", ""),
            ext_dir=d.get("ext_dir", ""),
        )


@dataclass
class Cache:
    """The complete on-disk state, mirrored one-to-one in cache.toml."""

    # tag -> CacheEntry for every installed Ghidra version.
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    # Which version to treat as "the" version. The literal string "latest"
    # means "track whatever the newest GitHub release is"; anything else pins
    # a specific tag.
    default: str = "latest"
    # The newest release tag we've seen from GitHub. Empty until the first
    # successful update check.
    latest_known: str = ""
    # Timestamp of the last successful update check (used to rate-limit checks).
    last_update_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # User preferences.
    prefs: Prefs = field(default_factory=Prefs)
    # The tag launched most recently, used to migrate preferences when switching.
    last_launched: str = ""

    def to_dict(self) -> dict:
        # Flatten the whole structure into TOML-serialisable primitives.
        return {
            "entries": {k: v.to_dict() for k, v in self.entries.items()},
            "default": self.default,
            "latest_known": self.latest_known,
            "last_update_check": self.last_update_check,
            "prefs": self.prefs.to_dict(),
            "last_launched": self.last_launched,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cache":
        # Rebuild the nested CacheEntry objects first.
        entries = {k: CacheEntry.from_dict(v) for k, v in d.get("entries", {}).items()}

        # last_update_check needs care: depending on how it was written it can
        # come back as a real datetime (tomllib parses native TOML datetimes),
        # as an ISO string, or be missing entirely. Normalise all three cases to
        # a timezone-aware UTC datetime so arithmetic in main() never blows up.
        raw_dt = d.get("last_update_check", None)
        if isinstance(raw_dt, datetime):
            # Native datetime: attach UTC if it's naive.
            last_update_check = raw_dt if raw_dt.tzinfo else raw_dt.replace(tzinfo=timezone.utc)
        elif isinstance(raw_dt, str) and raw_dt:
            # ISO-8601 string: parse it, defaulting to UTC if no offset present.
            try:
                last_update_check = datetime.fromisoformat(raw_dt)
                if last_update_check.tzinfo is None:
                    last_update_check = last_update_check.replace(tzinfo=timezone.utc)
            except ValueError:
                # Corrupt/unparseable value — fall back to "now".
                last_update_check = datetime.now(timezone.utc)
        else:
            # Missing or unexpected type — fall back to "now".
            last_update_check = datetime.now(timezone.utc)

        return cls(
            entries=entries,
            default=d.get("default", "latest"),
            latest_known=d.get("latest_known", ""),
            last_update_check=last_update_check,
            prefs=Prefs.from_dict(d.get("prefs", {})),
            last_launched=d.get("last_launched", ""),
        )


class Cacher:
    """Loads, holds, and saves the :class:`Cache`, plus a few helpers."""

    def __init__(self, cache: Cache, cache_path: Path) -> None:
        # The in-memory state and the file it is persisted to.
        self.cache = cache
        self.cache_path = cache_path

    @classmethod
    def load(cls, cache_path: Path) -> "Cacher":
        # First run: no file yet, so start from an empty Cache. It will be
        # written the first time save() is called.
        if not cache_path.exists():
            logger.info("No cache found, it will be created")
            return cls(Cache(), cache_path)

        # Otherwise read and parse the TOML. If anything goes wrong (corrupt
        # file, schema drift, ...) we log it and start fresh rather than crash —
        # losing the cache is recoverable, a hard crash on every command is not.
        try:
            with open(cache_path, "rb") as f:
                data = tomllib.load(f)
            cache = Cache.from_dict(data)
        except Exception as e:
            logger.error("Failed to load old cache: %s", e)
            cache = Cache()

        return cls(cache, cache_path)

    def save(self) -> None:
        # Serialise the current state and write it back out. tomli_w requires a
        # binary file handle.
        data = self.cache.to_dict()
        with open(self.cache_path, "wb") as f:
            tomli_w.dump(data, f)

    def default_explicit(self) -> str:
        """Resolve ``default`` to a concrete tag.

        ``default`` may be the sentinel "latest", in which case the real tag is
        whatever we last learned from GitHub. Note this can be an empty string
        if no update check has succeeded yet — callers that turn this into a
        download URL must guard against that.
        """
        if self.cache.default == "latest":
            return self.cache.latest_known
        return self.cache.default

    def is_installed(self, tag: str) -> bool:
        # A version is "installed" iff we have a cache entry keyed by its tag.
        # (An empty string is never a key, so is_installed("") is always False.)
        return tag in self.cache.entries
