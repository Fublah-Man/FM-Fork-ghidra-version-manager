# Code Review — Issues Found & Fixed

A full read-through of the `gvm/` package turned up the problems below. Every
item has been **fixed** on this branch, and the code throughout `gvm/` has been
commented to explain what each part does. Severity reflects user impact:

| #  | Severity | File(s)                          | Issue                                              | Status |
|----|----------|----------------------------------|----------------------------------------------------|--------|
| 1  | Critical | `main.py`                        | `update` could install an empty tag → 404 crash    | Fixed  |
| 2  | Critical | `install.py`                     | `parts[2]` index with no bounds check → `IndexError`| Fixed  |
| 3  | High     | `extensions.py`                  | Tar extraction path traversal (zip-slip)           | Fixed  |
| 4  | High     | `install.py`                     | `zipfile.extractall` path traversal                | Fixed  |
| 5  | High     | `install.py`                     | No download integrity verification                 | Fixed  |
| 6  | Medium   | `main.py`                        | `_resolve_tag` could yield an empty tag silently   | Fixed  |
| 7  | Medium   | `extensions.py`                  | Git download: no size/total, partial left on error | Fixed  |
| 8  | Medium   | `prefs_backup/backup_generator.py`| Bare `FileNotFoundError` on missing prefs         | Fixed  |
| 9  | Medium   | `main.py`                        | Broad `except Exception` masked real errors        | Fixed  |
| 10 | Low      | `gui.py`                         | TOCTOU race in single-instance lock                | Fixed  |
| 11 | Low      | `gui.py`                         | GUI restart `Popen` exit not checked               | Fixed  |
| 12 | Low      | `main.py`                        | Inconsistent launch behaviour across platforms     | Fixed  |
| 13 | Low      | `install.py`                     | PIL `Image.open` not closed                        | Fixed  |
| 14 | Low      | `install.py`                     | Unquoted interpolation into macOS launcher script  | Fixed  |
| 15 | Low      | `main.py`                        | `prefs set scale` unvalidated / unguarded `int()`  | Fixed  |
| 16 | Critical | `gvm/` (packaging)               | `python -m gvm` had no `__main__` — launchers broke | Fixed  |
| 17 | Low      | `cache.py`                       | Unused `import sys`                                 | Fixed  |

---

## Details

### 1. `update` could crash trying to install an empty tag — Critical
`main.py`'s `update` command read `latest = cacher.cache.latest_known`. When an
update check had never succeeded (offline, rate-limited) `latest_known` is `""`,
`is_installed("")` is `False`, and `install_version(..., "")` was called, which
built `.../releases/tags/` (no tag) and crashed on the 404.
**Fix:** guard for an empty `latest` and print a clear "run `gvm check-update`
first" message instead. Verified: `gvm --offline update` now logs the message.

### 2. Unchecked array index when parsing the zip name — Critical
`install.py` did `version = file_name.split("_")[2]`. A release whose tag didn't
fit `ghidra_<x>_<version>...` would raise an opaque `IndexError`.
**Fix:** validate `len(parts) >= 3` and raise a descriptive `RuntimeError`.

### 3. Tar extraction path traversal — High
`_install_processor_git` wrote each tar member to `base/ext_name/rel` with only
a `startswith(prefix)` check — a crafted archive with `..` segments could escape
the Processors directory.
**Fix:** resolve each output path and skip any member that isn't
`is_relative_to` the intended destination root.

### 4. `zipfile.extractall` path traversal — High
The Ghidra release zip was extracted with `zf.extractall(path)`, which honours
`..`/absolute entries.
**Fix:** new `_safe_extract_zip` validates every member (rejecting absolute
paths and anything that resolves outside the target) before extracting.

### 5. No download integrity verification — High
Release zips were extracted and trusted with no checksum check.
**Fix:** `_verify_digest` recomputes SHA-256 and compares against the GitHub
asset's `digest` field when present (newer API responses include it), aborting
and deleting the file on mismatch. When the API omits a digest there is nothing
to verify against, so the check is skipped with a debug note (documented as
best-effort).

### 6. `_resolve_tag` could silently return an empty tag — Medium
Related to #1: `"latest"`/`"default"` resolve to `latest_known`, which may be
empty. Rather than make the shared resolver raise (which would break the
graceful "Not found" path of `locate`), the **consuming** commands now guard the
empty value: `install_version`, `run`, and `update` each detect it and print a
clear message. Verified: `gvm --offline run` (no tag) reports cleanly instead of
raising `KeyError`.

### 7. Git extension download: no total, partial file on failure — Medium
`_install_processor_git` used `tqdm()` with no total and left a partial
`.tar.gz` if the stream failed.
**Fix:** derive a total from `Content-Length` when present and wrap the write in
`try/except` that `unlink(missing_ok=True)`s the partial file on error. The same
cleanup was added to `_install_download_only`.

### 8. Bare `FileNotFoundError` on missing preferences — Medium
`BackupGenerator.from_cached_version` called `read_bytes()` directly; if the user
hadn't launched the version yet the error was an unhelpful bare path.
**Fix:** explicit existence check raising a descriptive `FileNotFoundError`
("Launch this version at least once..."). CLI and GUI already catch it.

### 9. Broad exception handlers masked real errors — Medium
`do_update_check` caught `except Exception`, and the notification block swallowed
everything.
**Fix:** catch `requests.RequestException` for the network call and
`ImportError` (plyer optional) specifically, keeping only a narrow debug-level
fallback for genuinely unexpected notification-backend failures.

### 10. TOCTOU race in the single-instance lock — Low
`_acquire_lock` checked `lock.exists()` then later wrote the file — two GUIs
starting together could both win.
**Fix:** create the lock atomically with `os.open(..., O_CREAT | O_EXCL)`; on
`FileExistsError` inspect the recorded PID and only clear + retry if it's stale.
PID-liveness was factored into `_pid_is_running`.

### 11. GUI restart didn't check the spawned process — Low
`_restart_gui` ignored the `Popen` result.
**Fix:** `poll()` immediately after launch and log if the child exited at once.

### 12. Inconsistent launch behaviour across platforms — Low
`run` used `os.execv` on Linux but `Popen` elsewhere, leaving GVM lingering in
the foreground on Windows/macOS.
**Fix:** non-Linux now `Popen`s then `sys.exit(0)`, with a comment explaining
why `execv` isn't used (notably `.bat` isn't directly exec-able on Windows).

### 13. PIL image handle not closed — Low
`_ico_to_png` left the source image open.
**Fix:** wrap `Image.open` in a `with` block.

### 14. Unquoted interpolation into the macOS launcher script — Low
The generated `.app` script interpolated the interpreter path and tag raw.
**Fix:** `shlex.quote` both and `exec` the command.

### 15. `prefs set scale` unvalidated — Low
`int(args.value)` could raise `ValueError` on bad input and accepted any value.
**Fix:** guard the conversion and bound the value to 1–16 with clear errors.

### 16. `python -m gvm` was broken — Critical
Every launcher GVM generates (Linux `.desktop`, macOS `.app`) plus
`GVM_GUI.bat` and the README invoke `python -m gvm ...`, but the package had no
`__main__.py`, so the command failed with *"No module named gvm.__main__"* —
meaning no generated launcher actually worked.
**Fix:** added `gvm/__main__.py` delegating to `gvm.main.main`. Verified:
`python -m gvm prefs show` now runs.

### 17. Unused import — Low
`cache.py` imported `sys` without using it. **Fix:** removed.

---

## Notes / possible follow-ups (not changed)
- `gvm list` (and other direct API calls) still surface a raw traceback on
  network/HTTP errors such as a `403` rate-limit. Consider wrapping these in a
  friendly message, mirroring `do_update_check`.
- Downloads are unauthenticated against GitHub; heavy use may hit anonymous
  rate limits. A `GITHUB_TOKEN` env var could be honoured if set.
- Integrity verification (#5) is only as strong as GitHub publishing a digest;
  pinning known-good hashes per release would be stronger but higher-maintenance.
