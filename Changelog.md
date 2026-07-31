# Changelog

All notable changes to this project are documented here.

This is a Python fork of [CUB3D/ghidra-version-manager](https://github.com/CUB3D/ghidra-version-manager), originally written in Rust. The fork history begins at version 0.1.

---

## Python Fork (Fublah-Man)

### 0.4 - 2026-07-30

A full security, correctness and packaging audit with the fixes to match, plus a
test suite and cross-platform CI to keep it that way. Every issue below was
reproduced before it was fixed; see `AUDIT-2026-07.md` for the evidence and
`REMEDIATION-2026-07.md` for the change-by-change mapping.

#### Fixed - critical

- **The bundled extension registry shipped empty.** `extensions-repo/` lived at
  the repo root and was declared in neither `packages.find` nor `package-data`,
  so it was absent from every non-editable install. `Path.glob` on the missing
  directory returned nothing rather than raising, so `gvm extensions list`
  printed an empty list with no error — 0 of 27 extensions, silently. It now
  lives at `gvm/extensions-repo/` and is resolved with `importlib.resources`.
- **macOS installs crashed part-way through.** `res/macos_plist.plist` had the
  same packaging gap, and the read was unguarded — so it raised
  `FileNotFoundError` *after* Ghidra had been extracted and
  `/Applications/Ghidra_X.app` created, leaving an orphaned half-install.
- **A crafted release could write a launcher anywhere and inject a command into
  it.** The version string was taken from the release tag with only a
  segment-*count* check, so a tag shaped `a_<payload>_b` put `<payload>`
  verbatim into the `.desktop` path — `../../..` escaped the applications
  directory entirely. The Linux `Exec=` line was also unquoted (only the macOS
  launcher was quoted). Version strings are now validated and both launchers are
  shell-quoted.

#### Fixed - high

- **`--offline` still made network calls.** The implicit update check ignored the
  flag, costing a 30-second timeout per command on a disconnected machine.
- **An unavailable `install_dir` bricked every command.** Pointing it at a drive
  and unplugging that drive raised an unhandled `PermissionError` from every
  invocation — including `gvm prefs set install_dir default`, the command that
  would have fixed it. Read-only commands now continue; mutating ones exit with
  a reset instruction.
- **A truncated `cache.toml` silently reset you to a fresh install.** Writes were
  non-atomic and a parse failure discarded the file, losing every installed
  version, extension, preference and the default. Writes now go through a temp
  file and `os.replace`; a damaged cache is preserved as
  `cache.toml.corrupt-<timestamp>`.
- **Preference migration had no rollback.** The restore overwrote the destination
  with a bare `write_bytes` and no pre-image. It is now transactional: snapshot,
  atomic write, restore the snapshot on failure.
- **Missing download digests were ignored silently.** GitHub omitting a `digest`
  downgraded the install to unverified at *debug* log level. It is now a visible
  warning, `--require-digest` makes it fatal, and asset URLs are checked against
  a GitHub host allowlist over HTTPS.

#### Fixed - medium and low

- `_GH_NAME_RE` accepted `.` and `..` despite a comment claiming it blocked them,
  so `https://github.com/../../etc/repo` parsed to `('..', '..')`.
- `parse_git_url` silently discarded the hostname, so `https://evil.com/o/r`
  resolved to `github.com/o/r` — a different repository than the user asked for.
  Non-GitHub hosts are now rejected.
- Reuse of a previously downloaded zip was gated on `__debug__`, which is true in
  every normal run; it now requires `--use-cached` or `GVM_USE_CACHED_DOWNLOAD=1`.
- The release asset download had no `RequestException` handling (the metadata
  fetch did), so a dropped connection produced a raw traceback.
- Asset selection preferred a blind `assets[0]`; it now prefers an actual `.zip`.
- Emoji in log messages could raise `UnicodeEncodeError` on legacy Windows
  consoles; all log output is now ASCII, with a CI check to keep it that way.
- `apply_ui_scale` raised from `shutil.copy2` when `launch.properties` was
  missing; it now warns and skips.
- Local extension names made only of punctuation all collapsed to the slug
  `local-` and overwrote each other's registry files.
- Windows launches via `["cmd", "/c", runner]` rather than `shell=True`.
- The JDK check ran before the offline guard, so `--offline install` printed the
  whole JDK install guide before reporting the actual blocker.

#### Added

- **Test suite** (`tests/`, 115 network-free tests) covering packaging, hostile
  archives, cache round-trip and corruption recovery, URL and slug validation,
  offline behaviour, the state lock, preference rollback, and the service layer.
- **CI** across Windows, macOS and Linux on Python 3.11–3.14, plus a
  `packaged-install` job that builds a wheel, installs it into a clean venv
  outside the source tree and asserts the bundled data actually shipped — the
  check that would have caught both packaging failures above.
- `ruff` (blocking) and `mypy` (advisory) via a `dev` extra.
- `gvm/service.py` — frontend-agnostic operations shared by the CLI and GUI.
- `gvm/lockfile.py` — a shared state lock. The GUI holds it; the CLI refuses
  mutating commands while it's held, so the two can no longer overwrite each
  other's `cache.toml` changes.
- `gvm/gui_tasks.py` — the GUI's threading policy, testable without a display.
- `gvm/http.py` — `GITHUB_TOKEN` / `GH_TOKEN` support (60 → 5,000 requests/hour)
  and connection reuse across the several calls a command makes.
- `--require-digest` and `--use-cached` flags.
- `CONTRIBUTING.md`.

#### Changed

- Dependencies gained upper bounds (`requests<3`, `tqdm<5`, `tomli-w<2`,
  `pillow<13`).
- Removed a stale committed worktree copy of the repo
  (`.claude/worktrees/gallant-galileo-679816/`) and tracked `__pycache__`.

### 0.3 - 2026-05-16

#### Changed
- **GUI: Extensions tab reworked** — "Installed Extensions" now scans the selected Ghidra version's `Ghidra/Extensions/` directory on disk instead of reading from the GVM cache. "Available Extensions" now includes both the built-in registry and any local extensions discovered from the configured extensions directory (shown under a "Local Extensions" header). The "Scan Ext Dir" button refreshes the available list. Local extensions can be installed (copied into the Ghidra Extensions directory) with a single click.
- **GUI: Extension version info** — both the Installed and Local Available extension lists now display the extension version number and Ghidra compatibility version (parsed from `extension.properties` fields `version` and `createdOn`).
- **GUI: Extension update check** — a "Check For Updates" button in the Extensions toolbar compares installed extension versions against both latest GitHub releases (for registry extensions) and local source versions (for extensions from the configured extensions directory). When a newer version is found, a gold "Update" button appears below the "Install" button in the Available list. For registry extensions, this downloads the latest release; for local extensions, it re-copies from the source directory.
- **GUI: Extension uninstall** — each entry in the Installed Extensions list now has an "Uninstall" button to remove the extension from the Ghidra directory.

#### Improved
- **GUI: Version row separators** — thin horizontal lines now visually separate each version listing for clearer readability.
- **GUI: Version row layout** — release date and What's New toggle are positioned directly below the version name in a clean vertical stack.
- **GUI: What's New scroll lock** — scrolling inside a What's New text box no longer scrolls the parent version list.

### 0.2 - 2026-05-14

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
- **Single-instance lock** for the GUI: prevents launching multiple instances. Uses PID-based lock file with stale lock recovery.
- **Custom install directory** (`gvm prefs set install_dir <path>`): Ghidra versions can now be installed to a user-specified directory instead of the platform default. Use `gvm prefs set install_dir default` to reset. The GVM cache file always remains at the platform default location.
- **Extensions directory and scan** (`gvm prefs set ext_dir <path>` + `gvm extensions scan`): Point GVM at a folder of local Ghidra extensions (unpacked directories with `extension.properties` or `.zip` files) and scan to register them for a Ghidra version. Use `gvm prefs set ext_dir default` to clear.
- **`-py` flag for `gvm run`**: launch Ghidra with PyGhidra for a single run without changing the persistent `py3` preference. Usage: `gvm run -py` or `gvm run -py <version>`.
- **Windows settings backup/restore**: `gvm settings backup` and `gvm settings restore` now work on Windows (reading from `%APPDATA%\ghidra\<version>\preferences`). Previously these were blocked with "only supported on unix".
- **Windows automatic preference migration**: switching Ghidra versions via `gvm run` or `gvm update` now automatically backs up and restores preferences on Windows, matching the existing Linux/macOS behavior.

#### Changed
- README rewritten with full usage walkthrough, extension registry listing, platform behavior table, and feature parity comparison with the original Rust version.
- Added Claude Code disclaimer to README.
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
