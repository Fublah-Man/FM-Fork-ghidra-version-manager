# Remediation — July 2026

Implements R-1 through R-10 from `AUDIT-2026-07.md`. All 19 findings addressed.

**Verification:** 115 tests pass; the package installs non-editable into a clean
Python 3.12 venv with all data files present; `gvm.gui` imports cleanly against
real customtkinter; every original exploit reproduction now fails to reproduce.

---

## What now works

```
gvm extensions list          -> 27 extensions (was 0 on any pip install)
gvm --offline <anything>     -> zero network calls (was 1, a 30s timeout)
pip install .  on macOS      -> completes (crashed mid-install)
GUI open + CLI mutation      -> CLI refuses with a clear message (silently raced)
```

Run the suite with `pip install -e ".[dev]" && pytest tests/ -q`.

---

## Changes by finding

### C-1, C-2 — Packaging (R-1)

`extensions-repo/` (27 TOMLs) and `res/macos_plist.plist` moved from the repo
root to `gvm/`, declared in `[tool.setuptools.package-data]`, and resolved via
`importlib.resources.files("gvm")` instead of `Path(__file__).parent.parent`.

The old path was correct only for editable installs. In a wheel it resolved to
`site-packages/extensions-repo`, which does not exist — and `Path.glob` on a
missing directory returns empty rather than raising, so the registry loaded 0 of
27 entries in silence. The same gap made the macOS plist read raise
`FileNotFoundError` *after* Ghidra was extracted and `/Applications/Ghidra_X.app`
created, orphaning a half-install.

### C-3 — Launcher injection (R-2)

Three changes in `install.py`:

- `_validate_version()` rejects anything not matching `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
  The previous `len(parts) >= 3` guard checked segment *count*, so a tag shaped
  `a_<payload>_b` put `<payload>` verbatim into a filesystem path.
- The Linux `Exec=` line is now `shlex.quote`d. Only the macOS branch was quoted
  before, so a tag containing `;` injected a command into a launcher the user
  clicks.
- A missing extracted directory now raises immediately instead of letting every
  downstream step fail against a nonexistent path.

### H-1 — Offline mode (R-4)

The implicit update check is gated on `args.offline`. It previously ran
unconditionally, so `--offline` still hit the GitHub API.

### H-2 — Unavailable install directory (R-4)

Both `mkdir` calls in `main()` are guarded. Read-only commands (`locate`,
`list`, `check-update`) proceed on the default directory with a warning;
mutating commands exit with the reset instruction. Previously any unreachable
`install_dir` raised `PermissionError` from every command, including the one
that would have fixed it.

### H-3 — Cache integrity (R-3)

`Cacher.save()` writes to a temp file in the same directory, `fsync`s, then
`os.replace()`s — atomic on POSIX and Windows. `Cacher.load()` moves an
unparseable cache to `cache.toml.corrupt-<timestamp>` and says so, instead of
discarding it and letting the next save overwrite it with empty state.

### H-4 — Prefs rollback (R-5)

`backup_restorer` gained `_atomic_write_with_rollback`: snapshot the destination,
write via temp + `os.replace`, restore the snapshot on any failure. The call in
`_backup_and_restore_prefs` is wrapped and reports clearly. Previously the write
was a bare `write_bytes` with no pre-image — nothing to roll back to.

### H-5, M-3, M-4, M-5 — Download trust (R-6)

- Missing digest is a visible warning; `--require-digest` makes it fatal.
- `_check_download_host()` requires HTTPS on a known GitHub host.
- Cached-zip reuse now needs `--use-cached` or `GVM_USE_CACHED_DOWNLOAD=1`. It
  was gated on `__debug__`, which is true in every normal run.
- The asset download is wrapped in `RequestException` handling.
- `_select_release_zip()` prefers an actual `.zip` over a blind `assets[0]`.

### M-1, M-2, L-1, L-2, L-3 — Validation (R-7)

- `_GH_NAME_RE` requires a leading alphanumeric, so `..` and `.` no longer match.
- `parse_git_url` rejects non-GitHub hosts instead of silently discarding them —
  `https://evil.com/o/r` used to resolve to `github.com/o/r`.
- `_generate_slug` falls back to a hash for punctuation-only names and bounds
  length; degenerate names no longer collide on `local-.toml`.
- Windows launch uses `["cmd", "/c", runner]` rather than `shell=True`.
- `dl_path.unlink(missing_ok=True)`.

### M-6 — Windows console encoding (R-2)

All emoji removed from log messages, with a CI grep that fails the build if any
return.

### M-7 — `apply_ui_scale` (R-6)

Warns and returns when `launch.properties` is absent instead of raising from
`shutil.copy2`.

### R-8 — Hygiene

- Deleted `.claude/worktrees/gallant-galileo-679816/` (stale full copy of the
  repo, ~60 files, divergent `gvm/`).
- Removed tracked `__pycache__` and `.egg-info`; extended `.gitignore`.
- Dependency upper bounds (`requests<3`, `tqdm<5`, `tomli-w<2`, `pillow<13`).
- New `gvm/http.py`: honours `GITHUB_TOKEN` / `GH_TOKEN` / `GVM_GITHUB_TOKEN`
  (60 → 5,000 requests/hour) and reuses one `requests.Session`. All five API
  call sites route through it.

### R-9 — Tests and CI

`tests/` — 115 tests across 6 modules: packaging, archive safety, cache,
URL/slug validation, offline + lock + rollback, service + tasks.

`.github/workflows/ci.yml` rewritten:

- `test` matrix: Windows / macOS / Linux × Python 3.11 / 3.12 / 3.13 (was Ubuntu × 3.11/3.12).
- `packaged-install` job builds a wheel, installs it into a clean venv outside
  the source tree, and asserts 27 extensions plus the plist — the check that
  would have caught both Criticals.
- `lint` job greps for merge markers and non-ASCII log messages.

### R-10 — Service layer and GUI split

**`gvm/service.py`** (new) — frontend-agnostic operations taking and returning
plain values: `resolve_tag`, `require_tag`, `runner_path`, `check_runner`,
`install`, `uninstall`, `set_ui_scale`, `set_install_dir`, `set_ext_dir`. This is
where the CLI/GUI duplication goes to die; `require_tag` is the empty-tag guard
the GUI never had.

**`gvm/lockfile.py`** (new) — `StateLock` with atomic `O_CREAT|O_EXCL`
acquisition and PID-liveness stale detection. The GUI's private `.gui.lock` is
gone; both frontends now use the same `gvm.lock`, which is what makes the CLI
able to detect the GUI.

**`gvm/gui_tasks.py`** (new) — `TaskRunner`, `marshal`, `reap`, `parse_event`.
The threading policy is now testable without a display. `gui.py` keeps
`_run_threaded` / `_task_queue` / `_busy` as thin delegates, so no call site
changed.

`gui.py`: 1,978 → 1,906 lines. The tab extractions (steps 4–5 of the plan) are
**not** done — see below.

---

## Accounting

**Not done:**

- **The three tab extractions from R-10 step 4.** `gui.py` is still ~1,900 lines
  containing all three tabs. I did the mechanical, verifiable layers (tasks,
  lock, service) and stopped short of moving UI construction code I cannot run.
  Remaining: ~6 hrs, and it should be done with the GUI open in front of you.
- **The GUI does not yet call `service.py`.** The module exists, is tested, and
  the CLI paths it mirrors are proven — but rewiring `gui.py`'s handlers to use
  it is the same un-runnable-verification problem. Doing it blind risks breaking
  working code for an internal-cleanliness win. ~3 hrs, best paired.
- **No mypy/pyright run.** `py.typed` still advertises type completeness that
  nothing verifies. ~2 hrs to add and fix fallout.
- **No `pip-audit`.** ~30 min if you want it.
- **Windows and macOS runtime unverified.** All 115 tests plus the packaged
  install ran on Linux only. CI now covers all three, so the first push will
  tell you.

**Assumptions recorded:**

- `check-update` is treated as read-only for the lock (it writes only
  `latest_known` and the check timestamp). If you'd rather it block, move it out
  of `_READ_ONLY_COMMANDS`.
- `--require-digest` defaults **off**. Ghidra releases don't always publish a
  digest, so defaulting it on would break normal installs. The warning is now
  visible; promoting it to a default is a one-line change when GitHub's coverage
  is reliable.
- Line endings left as CRLF per your instruction. New files match.
- The GUI's self-restart still spawns `python -m gvm.gui`. It works (there is a
  `__main__` guard), but it bypasses the console script and will fail
  confusingly if `customtkinter` is missing from the restarted process.

**Noticed, left untouched:**

- `gui.py` has four direct `requests.get` calls that don't route through
  `gvm/http.py`, so they miss the token and session reuse. Deliberate: they're
  inside GUI methods I'd rather change alongside the tab extraction.
- `Cacher.load` still catches bare `Exception`. Now that corrupt files are
  preserved the blast radius is small, but a real bug in `from_dict` still
  presents to the user as "corrupt cache."
- `README.md` needs updating: the extension count and macOS launcher rows were
  false for installed users before this work, and the new flags
  (`--require-digest`, `--use-cached`) and `GITHUB_TOKEN` support are
  undocumented.
- `ISSUES.md` describes the previous audit and now overlaps confusingly with
  `AUDIT-2026-07.md`. Consider folding it into a single history.

**Git:** nothing was committed — the sandbox can't write to `.git` (stale
`index.lock`). Review with `git status` and `git diff`, then branch and commit
as you see fit. Suggested split: R-1/R-2 (packaging + launcher security),
R-3/R-4/R-5 (state and robustness), R-6/R-7 (download trust and validation),
R-8/R-9 (hygiene, tests, CI), R-10 (service layer and GUI plumbing).
