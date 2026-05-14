"""Ghidra Version Manager - CustomTkinter GUI."""

import argparse
import atexit
import html as html_mod
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from gvm.cache import Cacher, CacheEntry
from gvm.extensions import _load_all_extensions, _scan_ext_dir, _ext_uninstall
from gvm.install import install_version
from gvm.main import update_latest_version
from gvm.prefs_backup.backup_generator import BackupGenerator
from gvm.prefs_backup.backup_restorer import BackupRestorer

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Colors
_CLR_INSTALLED = "#2fa572"
_CLR_DEFAULT = "#1f6aa5"
_CLR_DANGER = "#d9534f"
_CLR_MUTED = "#888888"


def _default_gvm_path() -> Path:
    home = Path.home()
    if sys.platform == "win32":
        return home / "AppData" / "Local" / "gvm"
    return home / ".local" / "opt" / "gvm"


class GVMApp(ctk.CTk):
    """Main application window."""

    WIDTH = 960
    HEIGHT = 680

    def __init__(self) -> None:
        super().__init__()
        self.title("Ghidra Version Manager")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(800, 560)

        # --- Data ---
        self._default_path = _default_gvm_path()
        self._default_path.mkdir(parents=True, exist_ok=True)
        self.cacher = Cacher.load(self._default_path / "cache.toml")
        self._install_path = (
            Path(self.cacher.cache.prefs.install_dir)
            if self.cacher.cache.prefs.install_dir
            else self._default_path
        )
        self._install_path.mkdir(parents=True, exist_ok=True)

        self._task_queue: queue.Queue[str | None] = queue.Queue()
        self._busy = False
        self._releases: list[dict] = []
        self._show_all_versions = False
        self._INITIAL_VERSION_COUNT = 4
        self._pending_restart = False
        self._whats_new_cache: dict[str, str | None] = {}
        self._expanded_tags: set[str] = set()

        # --- Layout ---
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._tabs = ctk.CTkTabview(self)
        self._tabs.grid(row=0, column=0, padx=8, pady=(2, 0), sticky="nsew")

        self._tabs.add("Versions")
        self._tabs.add("Extensions")
        self._tabs.add("Settings")

        self._build_versions_tab()
        self._build_extensions_tab()
        self._build_settings_tab()

        # --- Status bar ---
        self._status_var = ctk.StringVar(value="Ready")
        self._status = ctk.CTkLabel(
            self, textvariable=self._status_var, anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self._status.grid(row=1, column=0, padx=12, pady=(2, 6), sticky="ew")

        # Start queue poller
        self._poll_queue()

        # Auto-refresh on launch
        self.after(200, self._refresh_versions)

    # ------------------------------------------------------------------
    # Versions tab
    # ------------------------------------------------------------------

    def _build_versions_tab(self) -> None:
        tab = self._tabs.tab("Versions")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # Top bar
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.grid_columnconfigure(2, weight=1)

        self._btn_refresh = ctk.CTkButton(top, text="Refresh", width=100, command=self._refresh_versions)
        self._btn_refresh.grid(row=0, column=0, padx=(0, 6))

        self._btn_check_update = ctk.CTkButton(top, text="Check for Updates", width=140, command=self._check_update)
        self._btn_check_update.grid(row=0, column=1, padx=(0, 6))

        self._lbl_latest = ctk.CTkLabel(top, text="", font=ctk.CTkFont(size=13))
        self._lbl_latest.grid(row=0, column=2, sticky="w", padx=6)

        # Default selector + Sort selector
        right_frame = ctk.CTkFrame(top, fg_color="transparent")
        right_frame.grid(row=0, column=3, sticky="e")

        ctk.CTkLabel(right_frame, text="Default:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 4))
        self._default_var = ctk.StringVar(value=self.cacher.cache.default)
        self._opt_default = ctk.CTkOptionMenu(
            right_frame, variable=self._default_var, values=["latest"], width=150,
            command=self._on_set_default,
        )
        self._opt_default.pack(side="left")

        ctk.CTkLabel(right_frame, text="Sort:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(12, 4))
        self._sort_var = ctk.StringVar(value="Newest")
        self._opt_sort = ctk.CTkOptionMenu(
            right_frame, variable=self._sort_var,
            values=["Newest", "Install Date"],
            width=120, command=lambda _: self._rebuild_version_rows(),
        )
        self._opt_sort.pack(side="left")

        # Scrollable version list
        self._ver_scroll = ctk.CTkScrollableFrame(tab)
        self._ver_scroll.grid(row=1, column=0, sticky="nsew")
        self._ver_scroll.grid_columnconfigure(0, weight=1)

        self._ver_widgets: list[ctk.CTkFrame] = []

    def _rebuild_version_rows(self) -> None:
        """Rebuild the version list from self._releases + cache."""
        for w in self._ver_widgets:
            w.destroy()
        self._ver_widgets.clear()

        installed = set(self.cacher.cache.entries.keys())
        default_tag = self.cacher.default_explicit()

        # Update default selector options
        opts = ["latest"] + sorted(installed)
        self._opt_default.configure(values=opts)
        self._default_var.set(self.cacher.cache.default)

        if self.cacher.cache.latest_known:
            self._lbl_latest.configure(text=f"Latest: {self.cacher.cache.latest_known}")

        # Sort releases
        sorted_releases = list(self._releases)
        sort_mode = self._sort_var.get()
        if sort_mode == "Install Date":
            sorted_releases = self._sort_by_install_date(sorted_releases)
        # "Newest" is the default order from the GitHub API — no re-sort needed.

        # Lazy-load: show only the first N releases unless expanded
        if self._show_all_versions:
            visible = sorted_releases
        else:
            visible = sorted_releases[: self._INITIAL_VERSION_COUNT]

        for i, rel in enumerate(visible):
            tag = rel["tag_name"]
            is_installed = tag in installed
            is_default = tag == default_tag
            is_expanded = tag in self._expanded_tags

            grid_row = i * 2
            row = ctk.CTkFrame(self._ver_scroll)
            row.grid(row=grid_row, column=0, sticky="ew", pady=(0, 2), padx=2)
            row.grid_columnconfigure(0, weight=1)

            # --- Header row: left info + right buttons ---
            header = ctk.CTkFrame(row, fg_color="transparent")
            header.grid(row=0, column=0, sticky="ew")
            header.grid_columnconfigure(0, weight=1)

            # Left side: tag name + badges inline
            left = ctk.CTkFrame(header, fg_color="transparent")
            left.grid(row=0, column=0, sticky="w")

            ctk.CTkLabel(
                left, text=tag, font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
            ).pack(side="left", padx=(8, 4), pady=(4, 0))

            if is_installed:
                ctk.CTkLabel(
                    left, text="installed", text_color=_CLR_INSTALLED,
                    font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=3, pady=(4, 0))
            if is_default:
                ctk.CTkLabel(
                    left, text="default", text_color=_CLR_DEFAULT,
                    font=ctk.CTkFont(size=11),
                ).pack(side="left", padx=3, pady=(4, 0))

            # Right side: action buttons
            btn_frame = ctk.CTkFrame(header, fg_color="transparent")
            btn_frame.grid(row=0, column=1, padx=4, pady=2, sticky="e")

            if is_installed:
                ctk.CTkButton(
                    btn_frame, text="Run", width=60, height=26,
                    command=lambda t=tag: self._run_ghidra(t, False),
                ).pack(side="left", padx=1)
                ctk.CTkButton(
                    btn_frame, text="Run (Py)", width=72, height=26,
                    command=lambda t=tag: self._run_ghidra(t, True),
                ).pack(side="left", padx=1)
                ctk.CTkButton(
                    btn_frame, text="Uninstall", width=72, height=26,
                    fg_color=_CLR_DANGER, hover_color="#c9302c",
                    command=lambda t=tag: self._uninstall_version(t),
                ).pack(side="left", padx=1)
            else:
                ctk.CTkButton(
                    btn_frame, text="Install", width=72, height=26,
                    command=lambda t=tag: self._install_version(t),
                ).pack(side="left", padx=1)

            # --- Subtitle: release name + date (directly below tag) ---
            subtitle_parts: list[str] = []
            release_name = rel.get("name", "")
            if release_name and release_name != tag:
                subtitle_parts.append(release_name)
            published = rel.get("published_at", "")
            if published:
                try:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    subtitle_parts.append(dt.strftime("%b %d, %Y"))
                except (ValueError, TypeError):
                    pass
            if subtitle_parts:
                ctk.CTkLabel(
                    row, text="  •  ".join(subtitle_parts),
                    font=ctk.CTkFont(size=11), text_color=_CLR_MUTED, anchor="w",
                ).grid(row=1, column=0, padx=(10, 8), pady=0, sticky="w")

            # --- What's New toggle (directly below subtitle) ---
            arrow_text = "▼" if is_expanded else "▶"
            toggle_btn = ctk.CTkButton(
                row, text=f"{arrow_text}  What's New", width=120, height=22,
                font=ctk.CTkFont(size=11), fg_color="transparent",
                text_color=_CLR_MUTED, hover_color=("gray75", "gray25"),
                anchor="w",
                command=lambda t=tag: self._toggle_whats_new(t),
            )
            toggle_btn.grid(row=2, column=0, padx=(6, 8), pady=(0, 2), sticky="w")

            # --- Expanded What's New content ---
            if is_expanded:
                content = self._whats_new_cache.get(tag)
                if content is None:
                    lbl = ctk.CTkLabel(
                        row, text="Loading...",
                        font=ctk.CTkFont(size=11), text_color=_CLR_MUTED, anchor="nw",
                    )
                    lbl.grid(row=3, column=0, padx=(16, 8), pady=(0, 6), sticky="ew")
                elif content == "":
                    lbl = ctk.CTkLabel(
                        row, text="What's New not available for this version.",
                        font=ctk.CTkFont(size=11), text_color=_CLR_MUTED, anchor="nw",
                    )
                    lbl.grid(row=3, column=0, padx=(16, 8), pady=(0, 6), sticky="ew")
                else:
                    text_box = ctk.CTkTextbox(
                        row, wrap="word", font=ctk.CTkFont(size=12),
                        height=250, activate_scrollbars=True,
                    )
                    text_box.grid(row=3, column=0, padx=(10, 8), pady=(0, 6), sticky="ew")
                    text_box.insert("1.0", content)
                    text_box.configure(state="disabled")
                    # Prevent scroll events from bubbling to the parent list
                    self._lock_scroll(text_box)

            # --- Separator line between rows ---
            if i < len(visible) - 1:
                sep = ctk.CTkFrame(self._ver_scroll, height=1, fg_color=("gray70", "gray30"))
                sep.grid(row=grid_row + 1, column=0, sticky="ew", padx=6, pady=(2, 2))
                self._ver_widgets.append(sep)

            self._ver_widgets.append(row)

        # "Show All Releases" button when there are hidden releases
        remaining = len(sorted_releases) - len(visible)
        if remaining > 0:
            next_grid = len(visible) * 2
            btn_more = ctk.CTkButton(
                self._ver_scroll,
                text=f"Show All Releases ({remaining} more)",
                width=260, height=30,
                command=self._expand_all_versions,
            )
            btn_more.grid(row=next_grid, column=0, pady=(6, 4))
            self._ver_widgets.append(btn_more)

    def _expand_all_versions(self) -> None:
        """Expand the version list to show all releases."""
        self._show_all_versions = True
        self._rebuild_version_rows()

    def _sort_by_install_date(self, releases: list[dict]) -> list[dict]:
        """Sort releases so installed versions come first (most recently installed
        at top), followed by uninstalled versions in their original order."""
        installed_rels: list[tuple[float, dict]] = []
        uninstalled_rels: list[dict] = []

        for rel in releases:
            tag = rel["tag_name"]
            entry = self.cacher.cache.entries.get(tag)
            if entry and entry.path:
                try:
                    mtime = Path(entry.path).stat().st_mtime
                except OSError:
                    mtime = 0.0
                installed_rels.append((mtime, rel))
            else:
                uninstalled_rels.append(rel)

        # Most recently installed first
        installed_rels.sort(key=lambda x: x[0], reverse=True)
        return [rel for _, rel in installed_rels] + uninstalled_rels

    @staticmethod
    def _lock_scroll(textbox: ctk.CTkTextbox) -> None:
        """Bind mouse-wheel events on *textbox* so they scroll the textbox
        and never propagate up to the parent scrollable frame."""
        inner = textbox._textbox  # underlying tk.Text widget

        def _on_wheel(event):
            # Scroll the textbox, then consume the event
            inner.yview_scroll(-1 * (event.delta // 120), "units")
            return "break"

        inner.bind("<MouseWheel>", _on_wheel)        # Windows / macOS
        inner.bind("<Button-4>", lambda e: (inner.yview_scroll(-3, "units"), "break")[-1])  # Linux up
        inner.bind("<Button-5>", lambda e: (inner.yview_scroll(3, "units"), "break")[-1])   # Linux down

    def _toggle_whats_new(self, tag: str) -> None:
        """Toggle the What's New panel for a version."""
        if tag in self._expanded_tags:
            self._expanded_tags.discard(tag)
            self._rebuild_version_rows()
        else:
            self._expanded_tags.add(tag)
            if tag not in self._whats_new_cache:
                # Show "Loading..." immediately, then fetch in background
                self._rebuild_version_rows()
                threading.Thread(
                    target=self._fetch_whats_new, args=(tag,), daemon=True,
                ).start()
            else:
                self._rebuild_version_rows()

    def _fetch_whats_new(self, tag: str) -> None:
        """Fetch the What's New content for a Ghidra version from GitHub."""
        import requests

        base = (
            "https://raw.githubusercontent.com/NationalSecurityAgency/ghidra/"
            f"{tag}/Ghidra/Configurations/Public_Release/src/global/docs/WhatsNew"
        )
        content = ""
        try:
            # Try .md first (Ghidra 11.3+), then .html (older versions)
            for ext in (".md", ".html"):
                resp = requests.get(
                    base + ext,
                    headers={"User-Agent": "gvm"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    raw = resp.text
                    if ext == ".html":
                        raw = self._html_to_plain(raw)
                    content = self._extract_whats_new_section(raw, tag)
                    break
        except Exception as e:
            logger.debug("Failed to fetch WhatsNew for %s: %s", tag, e)

        self._whats_new_cache[tag] = content
        # Schedule a UI rebuild on the main thread
        self.after(0, self._rebuild_version_rows)

    @staticmethod
    def _extract_whats_new_section(text: str, tag: str) -> str:
        """Extract the 'What's New in Ghidra X.Y' section from the full text."""
        lines = text.splitlines()
        start = None
        for i, line in enumerate(lines):
            if re.match(r"^#+ What.?s New", line, re.IGNORECASE):
                start = i
                break
        if start is not None:
            return "\n".join(lines[start:]).strip()
        # Fallback: return full text (trimmed)
        return text.strip()

    @staticmethod
    def _html_to_plain(html_text: str) -> str:
        """Convert simple HTML to readable plain text."""
        text = re.sub(r"<br\s*/?>", "\n", html_text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "  - ", text, flags=re.IGNORECASE)
        text = re.sub(r"<h[1-6][^>]*>", "\n## ", text, flags=re.IGNORECASE)
        text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_mod.unescape(text)
        # Collapse excessive blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Extensions tab
    # ------------------------------------------------------------------

    def _build_extensions_tab(self) -> None:
        tab = self._tabs.tab("Extensions")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # --- Target version selector ---
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(top, text="Target Ghidra version:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self._ext_ver_var = ctk.StringVar(value="")
        self._opt_ext_ver = ctk.CTkOptionMenu(top, variable=self._ext_ver_var, values=["(none)"], width=220,
                                              command=lambda _: self._refresh_installed_exts())
        self._opt_ext_ver.pack(side="left")
        ctk.CTkButton(top, text="Scan Ext Dir", width=120, command=self._scan_extensions).pack(side="right")

        # Left: available extensions
        left_label = ctk.CTkLabel(tab, text="Available Extensions", font=ctk.CTkFont(size=14, weight="bold"))
        left_label.grid(row=0, column=0, sticky="s", pady=(60, 0))

        self._ext_avail_scroll = ctk.CTkScrollableFrame(tab)
        self._ext_avail_scroll.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self._ext_avail_scroll.grid_columnconfigure(0, weight=1)

        # Right: installed extensions
        right_label = ctk.CTkLabel(tab, text="Installed Extensions", font=ctk.CTkFont(size=14, weight="bold"))
        right_label.grid(row=0, column=1, sticky="s", pady=(60, 0))

        self._ext_inst_scroll = ctk.CTkScrollableFrame(tab)
        self._ext_inst_scroll.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        self._ext_inst_scroll.grid_columnconfigure(0, weight=1)

        self._ext_avail_widgets: list[ctk.CTkFrame] = []
        self._ext_inst_widgets: list[ctk.CTkFrame] = []

        self.after(300, self._refresh_ext_tab)

    def _refresh_ext_tab(self) -> None:
        """Refresh both available and installed extension lists."""
        # Update version selector
        installed = sorted(self.cacher.cache.entries.keys())
        if installed:
            self._opt_ext_ver.configure(values=installed)
            if not self._ext_ver_var.get() or self._ext_ver_var.get() == "(none)":
                default = self.cacher.default_explicit()
                self._ext_ver_var.set(default if default in installed else installed[0])
        else:
            self._opt_ext_ver.configure(values=["(none)"])
            self._ext_ver_var.set("(none)")

        self._rebuild_avail_exts()
        self._refresh_installed_exts()

    def _rebuild_avail_exts(self) -> None:
        for w in self._ext_avail_widgets:
            w.destroy()
        self._ext_avail_widgets.clear()

        for i, ext in enumerate(_load_all_extensions()):
            row = ctk.CTkFrame(self._ext_avail_scroll)
            row.grid(row=i, column=0, sticky="ew", pady=1, padx=2)
            row.grid_columnconfigure(0, weight=1)

            kind_short = "DL" if ext.get("kind", "DownloadOnly") == "DownloadOnly" else "Git"
            ctk.CTkLabel(row, text=ext["name"], anchor="w", font=ctk.CTkFont(size=13)).grid(
                row=0, column=0, padx=8, pady=4, sticky="w"
            )
            ctk.CTkLabel(row, text=kind_short, text_color=_CLR_MUTED, font=ctk.CTkFont(size=11)).grid(
                row=0, column=1, padx=4
            )
            ctk.CTkButton(
                row, text="Install", width=70,
                command=lambda n=ext["name"]: self._install_extension(n),
            ).grid(row=0, column=2, padx=6, pady=3)

            self._ext_avail_widgets.append(row)

    def _refresh_installed_exts(self) -> None:
        for w in self._ext_inst_widgets:
            w.destroy()
        self._ext_inst_widgets.clear()

        ver = self._ext_ver_var.get()
        if not ver or ver == "(none)" or ver not in self.cacher.cache.entries:
            return

        exts = self.cacher.cache.entries[ver].extensions
        for i, (slug, ext_entry) in enumerate(sorted(exts.items())):
            row = ctk.CTkFrame(self._ext_inst_scroll)
            row.grid(row=i, column=0, sticky="ew", pady=1, padx=2)
            row.grid_columnconfigure(0, weight=1)

            display_name = slug.replace("local-", "").replace("-", " ").title()
            ctk.CTkLabel(row, text=display_name, anchor="w", font=ctk.CTkFont(size=13)).grid(
                row=0, column=0, padx=8, pady=4, sticky="w"
            )
            ctk.CTkButton(
                row, text="Remove", width=70, fg_color=_CLR_DANGER, hover_color="#c9302c",
                command=lambda s=slug: self._uninstall_extension(s),
            ).grid(row=0, column=1, padx=6, pady=3)

            self._ext_inst_widgets.append(row)

    # ------------------------------------------------------------------
    # Settings tab
    # ------------------------------------------------------------------

    def _build_settings_tab(self) -> None:
        tab = self._tabs.tab("Settings")
        tab.grid_columnconfigure(1, weight=1)

        row_idx = 0

        # --- Preferences section ---
        ctk.CTkLabel(tab, text="Preferences", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=row_idx, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 6)
        )
        row_idx += 1

        # PyGhidra
        ctk.CTkLabel(tab, text="Use PyGhidra:", font=ctk.CTkFont(size=13)).grid(
            row=row_idx, column=0, sticky="w", padx=(20, 6), pady=6
        )
        self._pyghidra_var = ctk.BooleanVar(value=self.cacher.cache.prefs.pyghidra)
        self._sw_pyghidra = ctk.CTkSwitch(tab, variable=self._pyghidra_var, text="",
                                          command=self._save_prefs)
        self._sw_pyghidra.grid(row=row_idx, column=1, sticky="w", pady=6)
        row_idx += 1

        # UI Scale
        ctk.CTkLabel(tab, text="UI Scale:", font=ctk.CTkFont(size=13)).grid(
            row=row_idx, column=0, sticky="w", padx=(20, 6), pady=6
        )
        self._scale_var = ctk.StringVar(value=str(self.cacher.cache.prefs.ui_scale_override))
        self._ent_scale = ctk.CTkEntry(tab, textvariable=self._scale_var, width=80)
        self._ent_scale.grid(row=row_idx, column=1, sticky="w", pady=6)
        self._ent_scale.bind("<Return>", lambda _: self._save_prefs())
        ctk.CTkButton(tab, text="Apply", width=60, command=self._save_prefs).grid(
            row=row_idx, column=2, padx=6
        )
        row_idx += 1

        # Install dir
        ctk.CTkLabel(tab, text="Install Directory:", font=ctk.CTkFont(size=13)).grid(
            row=row_idx, column=0, sticky="w", padx=(20, 6), pady=6
        )
        dir_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dir_frame.grid(row=row_idx, column=1, columnspan=2, sticky="ew", pady=6)
        dir_frame.grid_columnconfigure(0, weight=1)

        self._install_dir_var = ctk.StringVar(
            value=self.cacher.cache.prefs.install_dir or str(self._default_path)
        )
        self._ent_install_dir = ctk.CTkEntry(dir_frame, textvariable=self._install_dir_var)
        self._ent_install_dir.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(dir_frame, text="Browse", width=70, command=self._browse_install_dir).grid(
            row=0, column=1, padx=2
        )
        ctk.CTkButton(dir_frame, text="Reset", width=60, command=self._reset_install_dir).grid(
            row=0, column=2, padx=2
        )
        row_idx += 1

        # Extensions dir
        ctk.CTkLabel(tab, text="Extensions Directory:", font=ctk.CTkFont(size=13)).grid(
            row=row_idx, column=0, sticky="w", padx=(20, 6), pady=6
        )
        ext_dir_frame = ctk.CTkFrame(tab, fg_color="transparent")
        ext_dir_frame.grid(row=row_idx, column=1, columnspan=2, sticky="ew", pady=6)
        ext_dir_frame.grid_columnconfigure(0, weight=1)

        self._ext_dir_var = ctk.StringVar(value=self.cacher.cache.prefs.ext_dir or "")
        self._ent_ext_dir = ctk.CTkEntry(ext_dir_frame, textvariable=self._ext_dir_var,
                                         placeholder_text="Not set")
        self._ent_ext_dir.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(ext_dir_frame, text="Browse", width=70, command=self._browse_ext_dir).grid(
            row=0, column=1, padx=2
        )
        ctk.CTkButton(ext_dir_frame, text="Clear", width=60, command=self._clear_ext_dir).grid(
            row=0, column=2, padx=2
        )
        row_idx += 1

        # Separator
        ctk.CTkFrame(tab, height=2, fg_color=_CLR_MUTED).grid(
            row=row_idx, column=0, columnspan=3, sticky="ew", padx=10, pady=12
        )
        row_idx += 1

        # --- Backup / Restore section ---
        ctk.CTkLabel(tab, text="Settings Backup / Restore", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=row_idx, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 6)
        )
        row_idx += 1

        backup_frame = ctk.CTkFrame(tab, fg_color="transparent")
        backup_frame.grid(row=row_idx, column=0, columnspan=3, sticky="ew", padx=20, pady=6)

        ctk.CTkLabel(backup_frame, text="Version:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 6))
        self._backup_ver_var = ctk.StringVar(value="")
        self._opt_backup_ver = ctk.CTkOptionMenu(backup_frame, variable=self._backup_ver_var,
                                                 values=["(none)"], width=200)
        self._opt_backup_ver.pack(side="left", padx=(0, 12))
        ctk.CTkButton(backup_frame, text="Backup", width=100, command=self._do_backup).pack(side="left", padx=4)
        ctk.CTkButton(backup_frame, text="Restore", width=100, command=self._do_restore).pack(side="left", padx=4)

    # ------------------------------------------------------------------
    # Threading helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._task_queue.get_nowait()
                if msg is None:
                    # Task finished
                    self._busy = False
                    if self._pending_restart:
                        self._pending_restart = False
                        self._restart_gui()
                        return
                    self.cacher = Cacher.load(self._default_path / "cache.toml")
                    self._rebuild_version_rows()
                    self._refresh_ext_tab()
                    self._refresh_backup_versions()
                elif isinstance(msg, tuple) and msg[0] == "__update_available__":
                    self._busy = False
                    tag = msg[1]
                    self._set_status(f"New version available: {tag}")
                    self._prompt_update(tag)
                else:
                    self._set_status(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _run_threaded(self, fn, *args, **kwargs) -> None:
        if self._busy:
            self._set_status("Another operation is in progress...")
            return
        self._busy = True
        t = threading.Thread(target=self._thread_wrapper, args=(fn, *args), kwargs=kwargs, daemon=True)
        t.start()

    def _thread_wrapper(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            self._task_queue.put(f"Error: {e}")
        finally:
            self._task_queue.put(None)

    # ------------------------------------------------------------------
    # Version operations
    # ------------------------------------------------------------------

    def _refresh_versions(self) -> None:
        self._run_threaded(self._do_refresh_versions)

    def _do_refresh_versions(self) -> None:
        import requests
        self._task_queue.put("Fetching releases...")
        try:
            resp = requests.get(
                "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases",
                params={"per_page": 100},
                headers={"User-Agent": "gvm"},
                timeout=30,
            )
            resp.raise_for_status()
            self._releases = resp.json()
        except Exception as e:
            self._task_queue.put(f"Failed to fetch releases: {e}")
            return

        # Also update latest known
        try:
            update_latest_version(self.cacher)
        except Exception:
            pass

        self._task_queue.put(f"Loaded {len(self._releases)} releases")

    def _check_update(self) -> None:
        self._run_threaded(self._do_check_update)

    def _do_check_update(self) -> None:
        """Check whether a newer version of GVM itself is available upstream."""
        self._task_queue.put("Checking for GVM updates...")
        repo_dir = Path(__file__).resolve().parent.parent
        try:
            # Fetch latest commits from the remote
            subprocess.run(
                ["git", "fetch"],
                cwd=repo_dir, capture_output=True, timeout=30,
            )
            # Compare local HEAD to upstream tracking branch
            result = subprocess.run(
                ["git", "status", "-uno", "--porcelain=v2", "--branch"],
                cwd=repo_dir, capture_output=True, text=True, timeout=10,
            )
            behind = 0
            for line in result.stdout.splitlines():
                if line.startswith("# branch.ab"):
                    # Format: # branch.ab +<ahead> -<behind>
                    parts = line.split()
                    for p in parts:
                        if p.startswith("-"):
                            try:
                                behind = abs(int(p))
                            except ValueError:
                                pass
                    break

            if behind > 0:
                self._task_queue.put(("__update_available__", f"{behind} commit(s) behind"))
                return
            else:
                self._task_queue.put("GVM is up to date")
        except FileNotFoundError:
            self._task_queue.put("git not found — cannot check for GVM updates")
        except Exception as e:
            self._task_queue.put(f"Update check failed: {e}")

    def _prompt_update(self, info: str) -> None:
        """Show a dialog asking the user whether to update GVM."""
        answer = messagebox.askyesno(
            "GVM Update Available",
            f"Your GVM installation is {info}.\n\nWould you like to update?",
        )
        if answer:
            self._run_threaded(self._do_update_and_restart)
        else:
            self._set_status("GVM update skipped")

    def _do_update_and_restart(self) -> None:
        """Pull the latest GVM source, reinstall the package, then restart."""
        repo_dir = Path(__file__).resolve().parent.parent

        self._task_queue.put("Pulling latest GVM changes...")
        pull = subprocess.run(
            ["git", "pull"],
            cwd=repo_dir, capture_output=True, text=True, timeout=60,
        )
        if pull.returncode != 0:
            self._task_queue.put(f"git pull failed: {pull.stderr.strip()}")
            return

        self._task_queue.put("Reinstalling GVM package...")
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[gui]"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120,
        )
        if pip.returncode != 0:
            self._task_queue.put(f"pip install failed: {pip.stderr.strip()}")
            return

        self._task_queue.put("Updated — restarting...")
        # _thread_wrapper will append None next (clears _busy), then
        # the poll handler sees _pending_restart and restarts the GUI.
        self._pending_restart = True

    def _restart_gui(self) -> None:
        """Close the current window and re-launch the GUI in a new process."""
        _release_lock()
        self.destroy()
        subprocess.Popen([sys.executable, "-m", "gvm.gui"])

    def _install_version(self, tag: str) -> None:
        self._run_threaded(self._do_install_version, tag)

    def _do_install_version(self, tag: str) -> None:
        self._task_queue.put(f"Installing {tag}...")
        fake_args = argparse.Namespace(verbose=False, offline=False, launcher=False)
        install_version(self.cacher, fake_args, self._install_path, tag)
        self._task_queue.put(f"Installed {tag}")

    def _uninstall_version(self, tag: str) -> None:
        self._run_threaded(self._do_uninstall_version, tag)

    def _do_uninstall_version(self, tag: str) -> None:
        self._task_queue.put(f"Uninstalling {tag}...")
        if tag in self.cacher.cache.entries:
            entry = self.cacher.cache.entries[tag]
            shutil.rmtree(entry.path, ignore_errors=True)
            if entry.launcher:
                lp = Path(entry.launcher)
                if lp.is_dir():
                    shutil.rmtree(lp, ignore_errors=True)
                elif lp.exists():
                    lp.unlink()
            del self.cacher.cache.entries[tag]
            self.cacher.save()
            self._task_queue.put(f"Uninstalled {tag}")
        else:
            self._task_queue.put(f"{tag} is not installed")

    def _run_ghidra(self, tag: str, pyghidra: bool) -> None:
        entry = self.cacher.cache.entries.get(tag)
        if entry is None:
            self._set_status(f"{tag} is not installed")
            return

        ip = Path(entry.path)

        if pyghidra or self.cacher.cache.prefs.pyghidra:
            runner = ip / ("support/pyghidraRun" if sys.platform != "win32" else "support/pyghidraRun.bat")
        elif sys.platform != "win32":
            runner = ip / "ghidraRun"
        else:
            runner = ip / "ghidraRun.bat"

        if not runner.exists():
            self._set_status("Runner not found — was the install deleted?")
            return

        self.cacher.cache.last_launched = tag
        self.cacher.save()

        self._set_status(f"Launching {tag}...")
        if sys.platform == "linux":
            os.execv(str(runner), [str(runner)])
        else:
            subprocess.Popen([str(runner)])

    def _on_set_default(self, value: str) -> None:
        self.cacher.cache.default = value
        self.cacher.save()
        self._rebuild_version_rows()
        self._set_status(f"Default set to {value}")

    # ------------------------------------------------------------------
    # Extension operations
    # ------------------------------------------------------------------

    def _install_extension(self, name: str) -> None:
        ver = self._ext_ver_var.get()
        if not ver or ver == "(none)":
            self._set_status("Select a Ghidra version first")
            return
        self._run_threaded(self._do_install_extension, name, ver)

    def _do_install_extension(self, name: str, ghidra_version: str) -> None:
        self._task_queue.put(f"Installing extension {name}...")
        from gvm.extensions import find_by_name, _install_download_only, _install_processor_git

        try:
            entry = find_by_name(name)
        except ValueError as e:
            self._task_queue.put(str(e))
            return

        ghidra_ent = self.cacher.cache.entries.get(ghidra_version)
        if ghidra_ent is None:
            self._task_queue.put(f"Version {ghidra_version} not installed")
            return

        if entry["slug"] in ghidra_ent.extensions:
            self._task_queue.put(f"{name} is already installed")
            return

        kind = entry.get("kind", "DownloadOnly")
        if kind == "DownloadOnly":
            _install_download_only(self.cacher, self._install_path, entry, ghidra_version)
        elif kind == "ProcessorGit":
            _install_processor_git(self.cacher, self._install_path, entry, ghidra_version)

        self._task_queue.put(f"Installed {name}")

    def _uninstall_extension(self, slug: str) -> None:
        ver = self._ext_ver_var.get()
        if not ver or ver == "(none)":
            self._set_status("Select a Ghidra version first")
            return
        self._run_threaded(self._do_uninstall_extension, slug, ver)

    def _do_uninstall_extension(self, slug: str, ghidra_version: str) -> None:
        self._task_queue.put(f"Removing {slug}...")
        ghidra_entry = self.cacher.cache.entries.get(ghidra_version)
        if ghidra_entry is None:
            self._task_queue.put(f"Version {ghidra_version} not installed")
            return

        ext_entry = ghidra_entry.extensions.get(slug)
        if ext_entry is None:
            self._task_queue.put(f"Extension {slug} not found")
            return

        del ghidra_entry.extensions[slug]
        self.cacher.save()

        for f in ext_entry.files:
            p = Path(f)
            if p.exists():
                if p.is_file():
                    p.unlink(missing_ok=True)
                else:
                    shutil.rmtree(p, ignore_errors=True)

        self._task_queue.put(f"Removed {slug}")

    def _scan_extensions(self) -> None:
        ver = self._ext_ver_var.get()
        if not ver or ver == "(none)":
            self._set_status("Select a Ghidra version first")
            return
        self._run_threaded(self._do_scan_extensions, ver)

    def _do_scan_extensions(self, ghidra_version: str) -> None:
        ext_dir_str = self.cacher.cache.prefs.ext_dir
        if not ext_dir_str:
            self._task_queue.put("No extensions directory set — configure in Settings tab")
            return

        ext_dir = Path(ext_dir_str)
        if not ext_dir.is_dir():
            self._task_queue.put(f"Directory not found: {ext_dir}")
            return

        self._task_queue.put(f"Scanning {ext_dir}...")
        from gvm.cache import ExtEntry
        found = _scan_ext_dir(ext_dir)

        if not found:
            self._task_queue.put("No extensions found")
            return

        ghidra_entry = self.cacher.cache.entries.get(ghidra_version)
        if ghidra_entry is None:
            self._task_queue.put(f"Version {ghidra_version} not installed")
            return

        added = 0
        for ext in found:
            slug = f"local-{ext['name'].lower().replace(' ', '-')}"
            if slug not in ghidra_entry.extensions:
                ghidra_entry.extensions[slug] = ExtEntry(files=[ext["path"]])
                added += 1

        if added:
            self.cacher.save()
            self._task_queue.put(f"Added {added} extension(s)")
        else:
            self._task_queue.put("All extensions already registered")

    # ------------------------------------------------------------------
    # Settings operations
    # ------------------------------------------------------------------

    def _save_prefs(self) -> None:
        self.cacher.cache.prefs.pyghidra = self._pyghidra_var.get()
        try:
            self.cacher.cache.prefs.ui_scale_override = int(self._scale_var.get())
        except ValueError:
            self._set_status("UI scale must be an integer")
            return
        self.cacher.save()
        self._set_status("Preferences saved")

    def _browse_install_dir(self) -> None:
        d = filedialog.askdirectory(title="Select Install Directory")
        if d:
            resolved = Path(d).resolve()
            resolved.mkdir(parents=True, exist_ok=True)
            self._install_dir_var.set(str(resolved))
            self.cacher.cache.prefs.install_dir = str(resolved)
            self._install_path = resolved
            self.cacher.save()
            self._set_status(f"Install directory set to {resolved}")

    def _reset_install_dir(self) -> None:
        self._install_dir_var.set(str(self._default_path))
        self.cacher.cache.prefs.install_dir = ""
        self._install_path = self._default_path
        self.cacher.save()
        self._set_status("Install directory reset to default")

    def _browse_ext_dir(self) -> None:
        d = filedialog.askdirectory(title="Select Extensions Directory")
        if d:
            resolved = Path(d).resolve()
            self._ext_dir_var.set(str(resolved))
            self.cacher.cache.prefs.ext_dir = str(resolved)
            self.cacher.save()
            self._set_status(f"Extensions directory set to {resolved}")

    def _clear_ext_dir(self) -> None:
        self._ext_dir_var.set("")
        self.cacher.cache.prefs.ext_dir = ""
        self.cacher.save()
        self._set_status("Extensions directory cleared")

    def _refresh_backup_versions(self) -> None:
        installed = sorted(self.cacher.cache.entries.keys())
        if installed:
            self._opt_backup_ver.configure(values=installed)
            if not self._backup_ver_var.get() or self._backup_ver_var.get() == "(none)":
                default = self.cacher.default_explicit()
                self._backup_ver_var.set(default if default in installed else installed[0])
        else:
            self._opt_backup_ver.configure(values=["(none)"])
            self._backup_ver_var.set("(none)")

    def _do_backup(self) -> None:
        ver = self._backup_ver_var.get()
        if not ver or ver == "(none)":
            self._set_status("Select a version to back up")
            return
        if ver not in self.cacher.cache.entries:
            self._set_status(f"{ver} is not installed")
            return

        out = filedialog.asksaveasfilename(
            title="Save Settings Backup",
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
            initialfile=f"ghidra_settings_{ver}.zip",
        )
        if not out:
            return

        try:
            backup = BackupGenerator.from_cached_version(self.cacher.cache.entries[ver], ver)
            Path(out).write_bytes(backup.backup_data)
            self._set_status(f"Backup saved to {out}")
        except FileNotFoundError:
            self._set_status(f"No preferences found for {ver}")
        except Exception as e:
            self._set_status(f"Backup failed: {e}")

    def _do_restore(self) -> None:
        ver = self._backup_ver_var.get()
        if not ver or ver == "(none)":
            self._set_status("Select a version to restore to")
            return
        if ver not in self.cacher.cache.entries:
            self._set_status(f"{ver} is not installed")
            return

        src = filedialog.askopenfilename(
            title="Open Settings Backup",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        if not src:
            return

        try:
            BackupRestorer.from_path(Path(src)).restore_to_cached_version(
                self.cacher.cache.entries[ver]
            )
            self._set_status(f"Restored settings to {ver}")
        except Exception as e:
            self._set_status(f"Restore failed: {e}")


def _lock_path() -> Path:
    """Return the path to the single-instance lock file."""
    return _default_gvm_path() / ".gui.lock"


def _acquire_lock() -> bool:
    """Try to acquire the single-instance lock.

    Uses a PID-based lock file. If a lock file exists but the PID within
    it is no longer running, the stale lock is removed and re-acquired.
    Returns True if the lock was acquired, False if another instance is running.
    """
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)

    if lock.exists():
        try:
            old_pid = int(lock.read_text().strip())
            # Check if the process is still alive
            if sys.platform == "win32":
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, old_pid
                )
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return False  # Process still running
                # Process not found — stale lock
            else:
                os.kill(old_pid, 0)  # Raises OSError if process doesn't exist
                return False  # Process still running
        except (ValueError, OSError):
            pass  # Stale lock — remove and re-acquire

    lock.write_text(str(os.getpid()))
    return True


def _release_lock() -> None:
    """Remove the lock file."""
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def launch_gui() -> None:
    """Entry point for the GUI."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not _acquire_lock():
        # Show a message box even though we won't create the main window
        root = ctk.CTk()
        root.withdraw()
        messagebox.showinfo(
            "GVM Already Running",
            "Another instance of the Ghidra Version Manager GUI is already running.",
        )
        root.destroy()
        return

    atexit.register(_release_lock)

    app = GVMApp()
    app.mainloop()

    _release_lock()


if __name__ == "__main__":
    launch_gui()
