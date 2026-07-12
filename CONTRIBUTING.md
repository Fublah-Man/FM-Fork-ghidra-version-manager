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
pytest              # unit tests (network-free, fast)
ruff check gvm      # lint (also: ruff check --fix gvm)
mypy gvm            # type check (advisory; the GUI module is excluded)
python -m compileall gvm   # byte-compile everything
```

CI (`.github/workflows/ci.yml`) runs these on Python 3.11 and 3.12 on every
push and pull request, and additionally:

- **fails on leftover merge-conflict markers** (`<<<<<<<` / `=======` /
  `>>>>>>>`) in any tracked `.py` file, and
- smoke-tests the CLI (`gvm --help`, `gvm prefs show`).

`ruff` and `pytest` are **blocking**; `mypy` is currently **advisory**
(`continue-on-error`) so it surfaces type issues without failing the build.

## Tests

Tests live in `tests/` and must stay **network-free and deterministic** — use
`tmp_path`/`monkeypatch` fixtures rather than touching the real home directory
or making HTTP calls. The GUI (`gvm/gui.py`) is not unit-tested (it needs a
display); keep testable logic out of the GUI layer where practical.

When you fix a bug, add a test that would have caught it.

## Conventions

- Keep `ruff check gvm` clean (line length is intentionally not enforced).
- Prefer small, focused pull requests. **Base every PR on `master`** — do not
  stack PRs on each other's branches (GitHub won't retarget them to `master`
  automatically, and the changes can fail to land).
- Don't commit build artifacts or tool caches (`__pycache__/`, `*.egg-info/`,
  `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/` are all git-ignored).

## Extensions registry

Built-in extensions are TOML files under `extensions-repo/`. User-added
extensions (via the GUI's "Add from git url" or `gvm extensions scan`) are
written to the per-user data directory, **not** the packaged registry, so a
reinstall never wipes them and they never leak machine-specific paths into the
repo.
