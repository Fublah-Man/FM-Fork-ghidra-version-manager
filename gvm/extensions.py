"""Ghidra extension management.

GVM ships a small built-in registry of extensions as TOML files in the
``extensions-repo/`` directory. Each entry describes where to fetch the
extension from and how to install it:

  * ``DownloadOnly`` — download a release asset and hand it to the user to
    install through Ghidra's own "File -> Install Extensions" dialog.
  * ``ProcessorGit`` — fetch a git tarball and unpack a processor module
    directly into the Ghidra install's ``Ghidra/Processors`` directory.

This module also knows how to *scan* a user-supplied directory for extensions
that aren't in the registry (unpacked folders or .zip files containing an
``extension.properties`` manifest).
"""

import gzip
import logging
import shutil
import tarfile
from pathlib import Path

import requests
import tomllib

from gvm.cache import Cacher, ExtEntry

logger = logging.getLogger(__name__)

# The bundled registry of known extensions (one TOML file per extension).
EXTENSIONS_REPO = Path(__file__).parent.parent / "extensions-repo"


def _load_all_extensions() -> list[dict]:
    """Load every registry TOML file into a list of dicts (sorted by filename)."""
    exts = []
    for p in sorted(EXTENSIONS_REPO.glob("*.toml")):
        with open(p, "rb") as f:
            exts.append(tomllib.load(f))
    return exts


def find_by_name(name: str) -> dict:
    """Look up a registry extension by its display name (case-insensitive)."""
    for ext in _load_all_extensions():
        if ext["name"].lower() == name.lower():
            return ext
    raise ValueError(f"Failed to find {name}")


def handle_ext_cmd(cacher: Cacher, path: Path, args) -> None:
    """Dispatch the `gvm extensions <sub>` command to the right handler."""
    cmd = args.ext_cmd

    if cmd in ("list", "ls"):
        # Just print the names of every registry extension.
        logger.info("Known extensions:")
        for ext in _load_all_extensions():
            logger.info("- %s", ext["name"])

    elif cmd in ("install", "i"):
        _ext_install(cacher, path, args)

    elif cmd in ("uninstall", "rm"):
        _ext_uninstall(cacher, args)

    elif cmd == "scan":
        _ext_scan(cacher, args)


def _ext_install(cacher: Cacher, path: Path, args) -> None:
    """Install a registry extension into a given (installed) Ghidra version."""
    # Default to the configured default version when no target is given.
    ghidra_version = getattr(args, "ghidra_version", None) or cacher.default_explicit()

    if not cacher.is_installed(ghidra_version):
        logger.error("Version '%s' isn't installed!", ghidra_version)
        return

    # Resolve the extension definition from the registry.
    try:
        entry = find_by_name(args.name)
    except ValueError as e:
        logger.error("%s", e)
        return

    # Skip if this version already has the extension recorded.
    ghidra_ent = cacher.cache.entries[ghidra_version]
    if entry["slug"] in ghidra_ent.extensions:
        logger.error("That extension is already installed")
        return

    # Route to the installer matching the extension's declared kind.
    kind = entry.get("kind", "DownloadOnly")

    if kind == "DownloadOnly":
        _install_download_only(cacher, path, entry, ghidra_version)
    elif kind == "ProcessorGit":
        _install_processor_git(cacher, path, entry, ghidra_version)


def _install_download_only(
    cacher: Cacher, path: Path, entry: dict, ghidra_version: str
) -> None:
    """Download a release asset; the user installs it via Ghidra's UI."""
    logger.info("Installing download only extension")

    # Find the latest release of the extension's GitHub repo.
    rel_resp = requests.get(
        f"https://api.github.com/repos/{entry['repo_user']}/{entry['repo_repo']}/releases/latest",
        headers={"User-Agent": "gvm"},
    )
    rel_resp.raise_for_status()
    rel = rel_resp.json()

    assets = rel.get("assets", [])
    if not assets:
        raise RuntimeError("This tag doesn't have an asset attached")
    asset = assets[0]
    url = asset["browser_download_url"]
    asset_name = asset["name"]
    asset_size = asset.get("size", 0)

    logger.info("Downloading: %s -> %s", url, asset_name)
    dl_path = path / asset_name
    logger.info("Saving to: %s", dl_path)

    from tqdm import tqdm
    dl_resp = requests.get(url, stream=True, timeout=300)
    dl_resp.raise_for_status()
    # Stream to disk with a progress bar; remove the partial file on any error
    # so a failed download doesn't leave a corrupt asset lying around.
    try:
        with open(dl_path, "wb") as f, tqdm(total=asset_size, unit="B", unit_scale=True) as pbar:
            for chunk in dl_resp.iter_content(chunk_size=65536):
                f.write(chunk)
                pbar.update(len(chunk))
    except Exception:
        dl_path.unlink(missing_ok=True)
        raise

    # DownloadOnly extensions can't be auto-installed; point the user at the dialog.
    logger.info(
        "This extension requires manual installation, please install using "
        "File->Install Extensions and select:"
    )
    logger.info("%s", dl_path)

    # Record the downloaded file so uninstall can clean it up later.
    cacher.cache.entries[ghidra_version].extensions[entry["slug"]] = ExtEntry(
        files=[str(dl_path)]
    )
    cacher.save()


def _install_processor_git(
    cacher: Cacher, path: Path, entry: dict, ghidra_version: str
) -> None:
    """Fetch a git tarball and unpack a processor module into the install."""
    logger.info("Installing git processor extension")

    # GitHub serves a gzip tarball of any branch via the codeload endpoint.
    branch = entry.get("branch_name") or "master"
    url = (
        f"https://api.github.com/repos/{entry['repo_user']}/{entry['repo_repo']}"
        f"/tarball/{branch}"
    )

    dl_path = path / f"{entry['slug']}.tar.gz"
    logger.info("Saving to: %s", dl_path)

    from tqdm import tqdm
    dl_resp = requests.get(
        url,
        stream=True,
        timeout=300,
        headers={"User-Agent": "gvm"},
    )
    dl_resp.raise_for_status()
    # The tarball endpoint usually omits Content-Length, so derive a total when
    # available and fall back to an unbounded bar otherwise. Clean up the partial
    # file if the stream fails midway.
    total = int(dl_resp.headers.get("content-length", 0)) or None
    try:
        with open(dl_path, "wb") as f, tqdm(total=total, unit="B", unit_scale=True) as pbar:
            for chunk in dl_resp.iter_content(chunk_size=65536):
                f.write(chunk)
                pbar.update(len(chunk))
    except Exception:
        dl_path.unlink(missing_ok=True)
        raise

    logger.info("Download done")

    # We need the on-disk location of the target Ghidra install.
    cache_ent = cacher.cache.entries.get(ghidra_version)
    if cache_ent is None:
        raise RuntimeError(f"Version {ghidra_version} isn't known")

    # Processor modules live under Ghidra/Processors/<name>.
    base = Path(cache_ent.path) / "Ghidra" / "Processors"
    ext_entry = ExtEntry(files=[str(base / entry["name"])])
    logger.info("files: %s", ext_entry.files)

    no_prefix = entry.get("no_prefix", False)
    ext_name = entry["name"]

    # Where extracted files are allowed to land; used for the traversal check.
    dest_root = (base / ext_name).resolve()

    # GitHub tarballs wrap everything in a top-level "<user>-<repo>-<sha>/"
    # directory. We detect that prefix from the first entry, then strip it from
    # every member so files land directly under base/ext_name.
    with gzip.open(dl_path, "rb") as gz_f:
        # mode="r|" = streaming read of a non-seekable gzip stream.
        with tarfile.open(fileobj=gz_f, mode="r|") as tar:
            tmp_prefix = ""

            for member in tar:
                member_path = member.name

                # First, establish the prefix to strip.
                if not tmp_prefix:
                    if no_prefix:
                        # Strip the whole wrapper directory.
                        if member.isdir():
                            tmp_prefix = member_path.rstrip("/") + "/"
                    else:
                        # Strip up to and including the ".../<ext_name>/" dir,
                        # so only the module's own contents are kept.
                        if member_path.endswith(f"/{ext_name}/"):
                            tmp_prefix = member_path
                    continue

                # Only extract regular files that live under the prefix.
                if not member.isfile():
                    continue
                if not member_path.startswith(tmp_prefix):
                    continue

                rel = member_path[len(tmp_prefix):]
                out_path = base / ext_name / rel

                # Path-traversal guard: a malicious tar could contain ".." or
                # absolute components in `rel`. Resolve the final path and make
                # sure it stays within dest_root before writing anything.
                if not out_path.resolve().is_relative_to(dest_root):
                    logger.warning("Skipping unsafe path in archive: %s", member_path)
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)

                # Stream the member's bytes out to disk.
                f_obj = tar.extractfile(member)
                if f_obj is not None:
                    out_path.write_bytes(f_obj.read())
                    logger.info("%s", out_path)
                    ext_entry.files.append(str(out_path))

    # Record everything we wrote so uninstall can remove it.
    cacher.cache.entries[ghidra_version].extensions[entry["slug"]] = ext_entry
    cacher.save()


def _ext_uninstall(cacher: Cacher, args) -> None:
    """Remove a previously installed extension and the files it created."""
    ghidra_version = getattr(args, "ghidra_version", None) or cacher.default_explicit()

    # Resolve the extension definition (we need its slug to find the record).
    try:
        ext_def = find_by_name(args.name)
    except ValueError as e:
        logger.error("%s", e)
        return

    ghidra_entry = cacher.cache.entries.get(ghidra_version)
    if ghidra_entry is None:
        logger.error("Version %s isn't installed", ghidra_version)
        return

    ext_entry = ghidra_entry.extensions.get(ext_def["slug"])
    if ext_entry is None:
        logger.error(
            "The version %s doesn't have the extension %s installed",
            ghidra_version,
            ext_def["name"],
        )
        return

    # Drop the cache record first, then delete the tracked files/dirs.
    del ghidra_entry.extensions[ext_def["slug"]]
    cacher.save()

    for f in ext_entry.files:
        p = Path(f)
        if p.exists():
            if p.is_file():
                logger.info("rm %s", p)
                p.unlink(missing_ok=True)
            else:
                logger.info("rmdir %s", p)
                shutil.rmtree(p, ignore_errors=True)


def _parse_extension_properties(props_path: Path) -> dict[str, str]:
    """Parse a Ghidra extension.properties file into a dict.

    The format is simple ``key=value`` lines; blank lines and ``#`` comments are
    ignored.
    """
    props: dict[str, str] = {}
    for line in props_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    return props


def _scan_ext_dir(ext_dir: Path) -> list[dict]:
    """Walk ext_dir and discover extensions.

    Looks for:
      1. Directories containing an extension.properties file (unpacked extensions)
      2. .zip files that contain an extension.properties at their root (packed extensions)

    Returns a list of dicts with 'name', 'path', and 'source' keys.
    """
    import zipfile

    found: list[dict] = []

    for item in sorted(ext_dir.iterdir()):
        # --- Case 1: an unpacked extension directory ---
        if item.is_dir():
            props_file = item / "extension.properties"
            if props_file.is_file():
                # Manifest right at the top level.
                props = _parse_extension_properties(props_file)
                name = props.get("name", item.name)
                found.append({"name": name, "path": str(item), "source": "directory"})
            else:
                # Some extensions extract with a wrapper folder, so look one
                # level deeper for the manifest too.
                for sub in item.iterdir():
                    sub_props = sub / "extension.properties" if sub.is_dir() else None
                    if sub_props and sub_props.is_file():
                        props = _parse_extension_properties(sub_props)
                        name = props.get("name", sub.name)
                        found.append({"name": name, "path": str(sub), "source": "directory"})

        # --- Case 2: a packed extension .zip ---
        elif item.is_file() and item.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(item, "r") as zf:
                    # The manifest may be at the zip root or one level deep.
                    props_entry = None
                    for zi in zf.namelist():
                        basename = zi.rsplit("/", 1)[-1] if "/" in zi else zi
                        depth = zi.count("/")
                        if basename == "extension.properties" and depth <= 1:
                            props_entry = zi
                            break

                    if props_entry:
                        # Parse the manifest straight out of the zip.
                        raw = zf.read(props_entry).decode("utf-8", errors="replace")
                        props: dict[str, str] = {}
                        for line in raw.splitlines():
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, _, v = line.partition("=")
                                props[k.strip()] = v.strip()
                        name = props.get("name", item.stem)
                    else:
                        # No manifest — fall back to the file name.
                        name = item.stem

                    found.append({"name": name, "path": str(item), "source": "zip"})
            except zipfile.BadZipFile:
                # Not actually a valid zip; skip it rather than crash the scan.
                logger.warning("Skipping invalid zip: %s", item.name)

    return found


def _ext_scan(cacher: Cacher, args) -> None:
    """Scan the configured extensions dir and register what's found in the cache."""
    ext_dir_str = cacher.cache.prefs.ext_dir
    if not ext_dir_str:
        logger.error("No extensions directory configured. Set one with: gvm prefs set ext_dir <path>")
        return

    ext_dir = Path(ext_dir_str)
    if not ext_dir.is_dir():
        logger.error("Extensions directory does not exist: %s", ext_dir)
        return

    ghidra_version = getattr(args, "ghidra_version", None) or cacher.default_explicit()
    if not cacher.is_installed(ghidra_version):
        logger.error("Version '%s' isn't installed!", ghidra_version)
        return

    found = _scan_ext_dir(ext_dir)
    if not found:
        logger.info("No extensions found in %s", ext_dir)
        return

    ghidra_entry = cacher.cache.entries[ghidra_version]
    added = 0

    for ext in found:
        # Local extensions get a synthetic "local-<name>" slug to avoid clashing
        # with registry slugs.
        slug = f"local-{ext['name'].lower().replace(' ', '-')}"
        if slug in ghidra_entry.extensions:
            logger.debug("Already registered: %s", ext["name"])
            continue

        ghidra_entry.extensions[slug] = ExtEntry(files=[ext["path"]])
        logger.info("Added: %s (%s) -> %s", ext["name"], ext["source"], ext["path"])
        added += 1

    if added:
        cacher.save()
        logger.info("Registered %d extension(s) for %s", added, ghidra_version)
    else:
        logger.info("All extensions already registered")
