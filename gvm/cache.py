import logging
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tomli_w

logger = logging.getLogger(__name__)


@dataclass
class ExtEntry:
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"files": self.files}

    @classmethod
    def from_dict(cls, d: dict) -> "ExtEntry":
        return cls(files=d.get("files", []))


@dataclass
class CacheEntry:
    path: str = ""
    launcher: Optional[str] = None
    extensions: dict[str, ExtEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {"path": self.path}
        if self.launcher is not None:
            d["launcher"] = self.launcher
        if self.extensions:
            d["extensions"] = {k: v.to_dict() for k, v in self.extensions.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CacheEntry":
        exts = {k: ExtEntry.from_dict(v) for k, v in d.get("extensions", {}).items()}
        return cls(
            path=d.get("path", ""),
            launcher=d.get("launcher"),
            extensions=exts,
        )


@dataclass
class Prefs:
    pyghidra: bool = False
    ui_scale_override: int = 1
    install_dir: str = ""
    ext_dir: str = ""

    def to_dict(self) -> dict:
        d: dict = {"pyghidra": self.pyghidra, "ui_scale_override": self.ui_scale_override}
        if self.install_dir:
            d["install_dir"] = self.install_dir
        if self.ext_dir:
            d["ext_dir"] = self.ext_dir
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Prefs":
        return cls(
            pyghidra=d.get("pyghidra", False),
            ui_scale_override=d.get("ui_scale_override", 1),
            install_dir=d.get("install_dir", ""),
            ext_dir=d.get("ext_dir", ""),
        )


@dataclass
class Cache:
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    default: str = "latest"
    latest_known: str = ""
    last_update_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    prefs: Prefs = field(default_factory=Prefs)
    last_launched: str = ""

    def to_dict(self) -> dict:
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
        entries = {k: CacheEntry.from_dict(v) for k, v in d.get("entries", {}).items()}

        raw_dt = d.get("last_update_check", None)
        if isinstance(raw_dt, datetime):
            last_update_check = raw_dt if raw_dt.tzinfo else raw_dt.replace(tzinfo=timezone.utc)
        elif isinstance(raw_dt, str) and raw_dt:
            try:
                last_update_check = datetime.fromisoformat(raw_dt)
                if last_update_check.tzinfo is None:
                    last_update_check = last_update_check.replace(tzinfo=timezone.utc)
            except ValueError:
                last_update_check = datetime.now(timezone.utc)
        else:
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
    def __init__(self, cache: Cache, cache_path: Path) -> None:
        self.cache = cache
        self.cache_path = cache_path

    @classmethod
    def load(cls, cache_path: Path) -> "Cacher":
        if not cache_path.exists():
            logger.info("No cache found, it will be created")
            return cls(Cache(), cache_path)

        try:
            with open(cache_path, "rb") as f:
                data = tomllib.load(f)
            cache = Cache.from_dict(data)
        except Exception as e:
            logger.error("Failed to load old cache: %s", e)
            cache = Cache()

        return cls(cache, cache_path)

    def save(self) -> None:
        data = self.cache.to_dict()
        with open(self.cache_path, "wb") as f:
            tomli_w.dump(data, f)

    def default_explicit(self) -> str:
        if self.cache.default == "latest":
            return self.cache.latest_known
        return self.cache.default

    def is_installed(self, tag: str) -> bool:
        return tag in self.cache.entries
