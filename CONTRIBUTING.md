# Contributing to GVM

Thanks for helping improve the Ghidra Version Manager. This is a small Python
project; the workflow is intentionally lightweight.

## Setup

Requires Python 3.11+.

```bash
git clone https://github.com/Fublah-Man/FM-Fork-ghidra-version-manager
cd FM-Fork-ghidra-version-manager
pip install -e ".[dev]"          # editable install + dev tooling
```

The `dev` extra installs `pytest`, `ruff`, `mypy`, and `types-requests`.
Optional extras: `gui` (CustomTkinter) and `notifications` (plyer).

## Running the checks

```bash
pytest                     # unit tests (network-free, fast)
ruff check gvm tests       # lint (also: ruff check --fix gvm tests)
mypy gvm                   # type check (advisory; gui.py is excluded)
python -m compileall gvm   # byte-compile everything
```

CI (`.github/workflows/ci.yml`) runs on every push and pull request:

- **`test`** — the suite on Windows, macOS and Linux across Python 3.11–3.14.
- **`packaged-install`** — builds a wheel, installs it into a clean venv
  *outside* the source tree, and asserts the bundled registry (27 extensions)
  and the macOS plist template actually shipped. This job exists because a
  packaging regression once shipped a registry that loaded zero extensions on
  every real install while working fine in an editable checkout.
- **`lint`** — `ruff` (blocking), `mypy` (advisory), a merge-conflict-marker
  gate, and a check that no log message contains non-ASCII characters (emoji in
  a `logger` call raises `UnicodeEncodeError` on legacy Windows consoles).

`ruff` and `pytest` are **blocking**; `mypy` is **advisory** for now because
`gui.py` is excluded from checking until its logic moves into `gvm/service.py`.

## Tests

Tests live in `tests/` and must stay **network-free and deterministic** — use
the `tmp_path` / `monkeypatch` / `tmp_home` fixtures rather than touching the
real home directory or making HTTP calls. The GUI (`gvm/gui.py`) is not
unit-tested because it needs a display; its threading policy lives in
`gvm/gui_tasks.py` and its state operations in `gvm/service.py`, both of which
**are** tested. Keep testable logic out of the GUI layer.

When you fix a bug, add a test that would have caught it.

## Architecture notes

- `gvm/service.py` — frontend-agnostic operations (resolve a tag, install,
  uninstall, read/write preferences). Both the CLI and the GUI should call
  these rather than reimplementing them.
- `gvm/lockfile.py` — the shared state lock. The GUI holds it for its whole
  session; the CLI refuses mutating commands while it's held, because
  `cache.toml` is read-modify-write and concurrent writers lose each other's
  changes.
- `gvm/http.py` — all GitHub calls route through here for consistent headers,
  `GITHUB_TOKEN` support and connection reuse.

## Conventions

- Keep `ruff check gvm tests` clean (line length is intentionally not enforced).
- No emoji or non-ASCII in log messages.
- Prefer small, focused pull requests. **Base every PR on `master`** — do not
  stack PRs on each other's branches (GitHub won't retarget them to `master`
  automatically, and the changes can fail to land).
- Don't commit build artifacts or tool caches (`__pycache__/`, `*.egg-info/`,
  `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` are all git-ignored).

## Extensions registry

Built-in extensions are TOML files under **`gvm/extensions-repo/`** — inside the
package, and declared in `pyproject.toml`'s `package-data`. They must stay there:
at the repo root they are absent from every non-editable install.

User-added extensions (via the GUI's "Add from git url" or `gvm extensions scan`)
are written to the per-user data directory, **not** the packaged registry, so a
reinstall never wipes them and they never leak machine-specific paths into the
repo.
