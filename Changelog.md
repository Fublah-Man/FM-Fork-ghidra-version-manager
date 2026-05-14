# Changelog

All notable changes to this project are documented here.

This is a Python fork of [CUB3D/ghidra-version-manager](https://github.com/CUB3D/ghidra-version-manager), originally written in Rust. The fork history begins at version 0.1.

---

## Python Fork (Fublah-Man)

### 0.2 - 2026-05-11

#### Added
- **GUI: Version sorting** — added a Sort dropdown to the Versions tab toolbar (next to Default). Options: "Newest" (default, by release date) and "Install Date" (installed versions first, most recently installed at top, using the install directory's filesystem timestamp).
- **GUI: GVM self-update** — "Check for Updates" now checks for updates to GVM itself (via `git fetch` against the upstream repo), not Ghidra releases. When commits are available, a dialog asks "Would you like to update?" — selecting Yes runs `git pull` and `pip install -e .[gui]`, then automatically restarts the GUI. The "Refresh" button continues to refresh the Ghidra release list as before.
- **GUI: What's New panel** — each version listing now has a collapsible "▶ What's New" toggle. Clicking it fetches the WhatsNew document from the Ghidra GitHub repository (`.md` for 11.3+, `.html` with tag-stripping for older versions), caches the result, and displays it in a scrollable text box. Click again to collapse.

#### Improved
- **GUI: Lazy-load versions** — the Versions tab now shows only the 4 most recent releases on load. A "Show All Releases (X more)" button at the bottom expands the full list on demand, reducing initial clutter.
- **GUI: Compact version rows** — each row now displays the release name (e.g. "Ghidra 11.4") and publish date as a muted subtitle beneath the tag, replacing the previous empty-space layout. Buttons and badges are tighter with reduced padding and smaller heights.
- **GUI: Tighter layout** — raised the tab bar, reduced row/button/status-bar padding throughout for a cleaner, denser interface. Shortened the Default version dropdown width.

### 0.1 - 2026-05-11

#### Forked and rewritten
- Converted the entire codebase from Rust to Python. All functionality ported: version management, extension management, preferences, settings backup/restore, desktop launcher creation, and update notifications.
- Removed all Rust source and build files (`src/`, `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `build.rs`).

#### Fixed
- `pyproject.toml` compatibility: replaced the non-existent `setuptools.backends.legacy:build` backend with `setuptools.build_meta`, fixing `pip install -e .` on modern setuptools.
- PEP 621 compliance: moved `homepage`/`repository` to `[project.urls]` and fixed author fields to the standard `authors` list, resolving build validation errors.

#### Added
- **Graphical interface** (`gvm gui` or `gvm-gui`): a full CustomTkinter dark-mode GUI with three tabs — Versions (browse/install/run/uninstall, set default, check for updates), Extensions (install/remove from the built-in registry, scan local directory), and Settings (all preferences, directory config, backup/restore). Requires `pip install -e ".[gui]"`. All long-running operations (downloads, installs) run on background threads with status bar updates.
- **Custom install directory** (`gvm prefs set install_dir <path>`): Ghidra versions can now be installed to a user-specified directory instead of the platform default. Use `gvm prefs set install_dir default` to reset. The GVM cache file always remains at the platform default location.
- **Extensions directory and scan** (`gvm prefs set ext_dir <path>` + `gvm extensions scan`): Point GVM at a folder of local Ghidra extensions (unpacked directories with `extension.properties` or `.zip` files) and scan to register them for a Ghidra version. Use `gvm prefs set ext_dir default` to clear.
- **`-py` flag for `gvm run`**: launch Ghidra with PyGhidra for a single run without changing the persistent `py3` preference. Usage: `gvm run -py` or `gvm run -py <version>`.
- **Windows settings backup/restore**: `gvm settings backup` and `gvm settings restore` now work on Windows (reading from `%APPDATA%\ghidra\<version>\preferences`). Previously these were blocked with "only supported on unix".
- **Windows automatic preference migration**: switching Ghidra versions via `gvm run` or `gvm update` now automatically backs up and restores preferences on Windows, matching the existing Linux/macOS behavior.

#### Changed
- README rewritten with full usage walkthrough, extension registry listing, platform behavior table, and feature parity comparison with the original Rust version.
- `.gitignore` updated to ignore `.claude/` directory.
- Version reset to 0.1 to reflect a new fork with its own versioning.
- Authors updated to list the fork maintainer (Fublah-Man) alongside the original author (CUB3D).
- Project URLs updated to link both the Python fork and the original Rust repository.

#### Improvements over the original Rust version

| Feature | Rust (original) | Python (this fork) |
|---|---|---|
| Preferences backup/restore | Linux/macOS only | All platforms |
| Auto-migrate prefs on version switch | Linux/macOS only | All platforms |
| One-shot PyGhidra launch (`-py`) | Not available | Available |
| Custom install directory | Not available | Available |
| Graphical interface | Not available | Available (CustomTkinter) |
| Local extensions directory + scan | Not available | Available |
| Install method | Requires Rust toolchain | `pip install` with Python 3.11+ |

---

## Original Rust Version (CUB3D)

### 0.7.1
- Fixed desktop entry to use PNG rather than ICO for icon, fixing corruption on Gnome

### 0.7.0
- New command `gvm locate` to get the path to a Ghidra install directory

### 0.6.0
- `gvm update` will now automatically backup and restore preferences from the old version to the new one
  - This also applies to automatic updates from `gvm run`
- Installation will no longer try and cache downloads for release builds, this prevents `Could not find EOCD` errors when resuming after an interrupted download

### 0.5.0
- Experimental unix-only support for backing up and restoring Ghidra preferences

### 0.4.0
- Support rewriting launch properties, prefs to set default ui scale `prefs set scale 2`

### 0.3.2
- Don't panic when the update check fails due to network issues

### 0.3.1
- Don't panic when deleting an extension you don't have installed

### 0.3.0
- Windows support
- Fixed `run latest` not detecting an existing install
- Now warns if you don't have java

### 0.2.2
- Fixed error on first run

### 0.2.1
- Launchers now proxy through gvm, so don't need to reinstall for new features
- Support for launching pyghidra via `prefs set py3 true`
- Update notifications when launching via desktop entries
