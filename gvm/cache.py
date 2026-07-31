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
import os
import tempfile
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
    # The upstream release tag this was installed from, when known. Used to tell
    # whether a newer release is available (compared tag-to-tag). Empty for
    # extensions installed before this was tracked.
    tag: str = ""

    def to_dict(self) -> dict:
        # Serialise to the shape stored in the TOML file.
        d: dict = {"files": self.files}
        if self.tag:
            d["tag"] = self.tag
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExtEntry":
        # Rebuild from TOML data; tolerate a missing "files" key (old caches).
        return cls(files=d.get("files", []), tag=d.get("tag", ""))


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
        # Reconstruct nested ExtEntry objects from the raw dicts. Skip any single
        # malformed extension record rather than letting it blow away the whole
        # cache (Cacher.load resets everything on an unhandled exception).
        exts: dict[str, ExtEntry] = {}
        for k, v in d.get("extensions", {}).items():
            if isinstance(v, dict):
                exts[k] = ExtEntry.from_dict(v)
            else:
                logger.warning("Skipping malformed extension record %r", k)
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
    # When True (the default), launching Ghidra from the GUI spawns it as a child
    # process so the GUI window stays open. When False the GUI is replaced/closed
    # by the launch (the older behaviour). Only consulted by the GUI.
    keep_gui_open: bool = True

    def to_dict(self) -> dict:
        # Always persist the two scalar settings; only persist the directory
        # overrides when set so an unset value round-trips as "" not "<cwd>".
        d: dict = {"pyghidra": self.pyghidra, "ui_scale_override": self.ui_scale_override}
        if self.install_dir:
            d["install_dir"] = self.install_dir
        if self.ext_dir:
            d["ext_dir"] = self.ext_dir
        # Only written when turned off, so the default-True case stays implicit.
        if not self.keep_gui_open:
            d["keep_gui_open"] = self.keep_gui_open
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Prefs":
        # Each field falls back to its default if absent from the TOML.
        # Coerce ui_scale_override to int so a hand-edited string value doesn't
        # later blow up `prefs show`'s "%d" formatting.
        try:
            scale = int(d.get("ui_scale_override", 1))
        except (TypeError, ValueError):
            scale = 1
        return cls(
            pyghidra=bool(d.get("pyghidra", False)),
            ui_scale_override=scale,
            install_dir=d.get("install_dir", ""),
            ext_dir=d.get("ext_dir", ""),
            keep_gui_open=bool(d.get("keep_gui_open", True)),
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
        # file, schema drift, ...) we start fresh rather than crash — a hard
        # crash on every command is worse than a lost cache.
        #
        # But we do NOT silently discard the damaged file. It records every
        # installed version, every installed extension, the default version and
        # all preferences; the next save() would overwrite it with empty state
        # and destroy any chance of manual recovery. Move it aside instead and
        # tell the user exactly where it went.
        try:
            with open(cache_path, "rb") as f:
                data = tomllib.load(f)
            cache = Cache.from_dict(data)
        except Exception as e:
            salvaged = cls._preserve_corrupt(cache_path)
            logger.error("Failed to load cache (%s)", e)
            if salvaged is not None:
                logger.error(
                    "The previous cache was damaged. It has been kept at %s so "
                    "you can recover installed-version records from it; GVM is "
                    "starting from an empty cache.",
                    salvaged,
                )
            cache = Cache()

        return cls(cache, cache_path)

    @staticmethod
    def _preserve_corrupt(cache_path: Path) -> Optional[Path]:
        """Move an unparseable cache aside so it isn't overwritten. Best-effort."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = cache_path.with_name(f"{cache_path.name}.corrupt-{stamp}")
        try:
            os.replace(cache_path, target)
            return target
        except OSError as e:
            logger.debug("Couldn't preserve corrupt cache: %s", e)
            return None

    def save(self) -> None:
        """Persist the cache atomically.

        Writing straight to ``cache_path`` truncates it the instant the handle
        opens, so a crash, power loss or full disk mid-write left a corrupt file
        — and ``load`` then silently reset the user to a fresh install. Write to
        a sibling temp file, flush it to disk, then ``os.replace`` (atomic on
        POSIX and on Windows) so the cache is only ever fully-old or fully-new.
        """
        data = self.cache.to_dict()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Same directory as the target: os.replace is only atomic within a
        # filesystem, and the temp dir may well be on a different one.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.cache_path.parent),
            prefix=f".{self.cache_path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                tomli_w.dump(data, f)
                f.flush()
                # Force the bytes out before the rename, so a crash immediately
                # after replace() can't leave a rename pointing at empty data.
                os.fsync(f.fileno())
            os.replace(tmp_path, self.cache_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

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
