"""Download, extract and register a Ghidra release.

This module owns the "install" side of GVM: it checks that a JDK is present,
fetches the release metadata from the GitHub API, downloads the release zip,
verifies and extracts it, creates a platform-appropriate desktop launcher,
patches Ghidra's ``launch.properties`` (for the UI-scale override), and finally
records everything in the cache.
"""

import hashlib
import logging
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

from gvm.cache import CacheEntry, Cacher
from gvm.ghidra_props_parser import GhidraPropsFile

logger = logging.getLogger(__name__)


def _verify_digest(file_path: Path, asset: dict) -> None:
    """Best-effort integrity check of a downloaded asset.

    Newer GitHub API responses include a ``digest`` field on each asset of the
    form ``"sha256:<hex>"``. When present we recompute the SHA-256 of the file
    we just downloaded and abort if it doesn't match — this catches truncated
    downloads and tampering in transit. Older API responses omit ``digest``;
    in that case there is nothing to check against, so we simply skip (and say
    so at debug level) rather than failing.
    """
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        logger.debug("No sha256 digest published for %s; skipping integrity check",
                     asset.get("name", file_path.name))
        return

    expected = digest.split(":", 1)[1].strip().lower()

    # Stream the file through the hasher in chunks so we don't load a multi-
    # hundred-megabyte zip fully into memory.
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest().lower()

    if actual != expected:
        # Remove the corrupt/tampered file so a later run won't reuse it.
        file_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {file_path.name}: "
            f"expected {expected}, got {actual}"
        )
    logger.debug("Checksum verified for %s", file_path.name)


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    """Extract *zip_path* into *dest* with path-traversal protection.

    ``zipfile.extractall`` will happily honour entries containing ``..`` or
    absolute paths, letting a malicious archive write files anywhere on disk
    (a "zip slip"). We validate every member up front and refuse the whole
    archive if any entry would escape *dest*.
    """
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # Reject obvious absolute paths early.
            name = info.filename
            if name.startswith("/") or name.startswith("\\"):
                raise RuntimeError(f"Unsafe absolute path in archive: {name}")
            # Resolve where this member would land and confirm it stays under
            # dest. ``is_relative_to`` (3.9+) does the containment check.
            target = (dest_resolved / name).resolve()
            if not target.is_relative_to(dest_resolved):
                raise RuntimeError(f"Unsafe path in archive escapes target dir: {name}")
        # All members validated — safe to extract.
        zf.extractall(dest)


def do_java_check() -> None:
    """Warn (loudly) if a JDK isn't available.

    Ghidra needs the full JDK (``javac`` specifically), not just a JRE. We probe
    by running ``javac --version``; if that succeeds we return silently. If it
    fails we print platform-specific installation hints. Note this only warns —
    installation still proceeds, because the user may install Java afterwards.
    """
    try:
        result = subprocess.run(
            ["javac", "--version"], capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # javac missing entirely, or hung — fall through to the hint block.
        pass

    logger.error("------------------------------")
    logger.error("You need to have the Java JDK (not JRE) installed to use Ghidra.")
    logger.error(
        "We tried to run `javac --version` but it failed, consider installing JDK "
        "(for Ghidra 11+ use version 21) LTS from the following:"
    )
    # Tailor the suggested command to the current OS.
    if sys.platform == "win32":
        logger.error("https://adoptium.net/temurin/releases")
    elif sys.platform == "darwin":
        logger.error("brew install openjdk@21")
    else:
        logger.error("sudo apt install default-jdk (Debian/Ubuntu)")
        logger.error("sudo pacman -Sy jdk21-openjdk (Arch)")
        logger.error("sudo dnf install java-21-openjdk-devel (Fedora/RHEL/Rocky)")
        logger.error(
            "sudo rpm-ostree install java-21-openjdk-devel (Fedora Silverblue/Kinoite)"
        )
        logger.error(
            "Add javaPackages.compiler.openjdk21 /etc/nix/configuration.nix and run "
            "`nixos-rebuild switch` (NixOS)"
        )
        logger.error("sudo emerge --ask --oneshot virtual/jdk (Gentoo)")
    logger.error("------------------------------")


def install_version(cacher: Cacher, args, path: Path, tag: str) -> None:
    """Install the Ghidra release identified by *tag* into *path*.

    *tag* may be a concrete release tag, or one of the sentinels "default" /
    "latest" which are resolved here against the cache.
    """
    # Nudge the user about the JDK requirement before we do any heavy work.
    do_java_check()

    logger.debug("Installing tag '%s'", tag)
    # Already installed? Nothing to do. (Sentinels aren't keys, so they fall
    # through to the resolution step below.)
    if tag in cacher.cache.entries:
        logger.info("That version is already installed")
        return

    # Resolve the "default"/"latest" sentinels to a concrete tag.
    if tag == "default":
        tag = cacher.default_explicit()
    elif tag == "latest":
        tag = cacher.cache.latest_known

    # Guard against an empty tag: this happens when "latest"/"default" resolve
    # to latest_known before any successful update check. Without this check we
    # would build the URL .../releases/tags/ (no tag) and get a confusing 404.
    if not tag:
        logger.error(
            "No version specified and the latest version isn't known yet. "
            "Run `gvm check-update` first, or pass an explicit tag."
        )
        return
    logger.debug("Installing actual tag '%s'", tag)

    # Fetch the release metadata for this exact tag from GitHub.
    resp = requests.get(
        f"https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/tags/{tag}",
        headers={"User-Agent": "gvm"},
    )
    resp.raise_for_status()
    release = resp.json()

    # Ghidra publishes a single zip asset per release; bail clearly if absent.
    assets = release.get("assets", [])
    if not assets:
        raise RuntimeError("This tag doesn't have an asset attached")
    asset = assets[0]
    url = asset["browser_download_url"]
    asset_size = asset.get("size", 0)

    logger.info("⬇️  Downloading: %s", url)

    # Save the download next to the install dir, named after the *real* tag.
    dl_path = path / f"ghidra_{release['tag_name']}.zip"
    logger.info("💾 Saving to %s", dl_path)

    if dl_path.exists() and __debug__:
        # In a normal (non -O) run we reuse a previously downloaded zip to speed
        # up repeated installs during development.
        logger.info("Using cached download")
    elif not getattr(args, "offline", False):
        # Stream the download to disk with a progress bar sized to the asset.
        dl_resp = requests.get(url, stream=True, timeout=300)
        dl_resp.raise_for_status()
        with (
            open(dl_path, "wb") as f,
            tqdm(total=asset_size, unit="B", unit_scale=True) as pbar,
        ):
            for chunk in dl_resp.iter_content(chunk_size=65536):
                f.write(chunk)
                pbar.update(len(chunk))
    else:
        # --offline was passed and we have no cached copy: can't continue.
        logger.error("Offline and no cached version found")
        return

    # Verify the download's integrity before trusting its contents.
    _verify_digest(dl_path, asset)

    logger.info("📦 Extracting to %s", path)

    try:
        # Use the path-traversal-safe extractor rather than raw extractall.
        _safe_extract_zip(dl_path, path)
    except zipfile.BadZipFile as e:
        # A truncated/corrupt download — delete it so the next run re-fetches.
        dl_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not open zip file, deleting: {e}") from e
    except Exception as e:
        # Any other extraction failure (including an unsafe-path rejection):
        # clean up the partial download and surface the cause.
        dl_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not extract zip file, deleting: {e}") from e

    logger.info("⚙️  Creating application launcher entries")

    # Derive the version string from the zip filename, which looks like
    # "ghidra_<tag>.zip" where <tag> is e.g. "Ghidra_11.4_build" → parts:
    # ["ghidra", "Ghidra", "11.4", "build.zip"] and parts[2] is the version.
    # Validate the shape first so a surprising tag format raises a clear error
    # instead of an opaque IndexError.
    file_name = dl_path.name
    parts = file_name.split("_")
    if len(parts) < 3:
        raise RuntimeError(
            f"Unexpected release zip name '{file_name}'; "
            "expected the form 'ghidra_<...>_<version>...zip'"
        )
    version = parts[2]
    dir_name = f"ghidra_{version}_PUBLIC"

    # Ghidra historically used a "_PUBLIC" suffix on the extracted folder; fall
    # back to the un-suffixed name for older releases.
    dir_path = dl_path.parent / dir_name
    if not dir_path.exists():
        logger.info("Failed to find extract, trying old style without suffix")
        dir_path = dl_path.parent / f"ghidra_{version}"

    # The launcher will invoke this Python interpreter to re-enter GVM in
    # "launcher" mode and run the chosen version.
    us = sys.executable
    exec_cmd = f"{us} -m gvm --launcher run {tag}"

    ico_file_path = dir_path / "support" / "ghidra.ico"

    launcher: Path | None = None

    if sys.platform == "linux":
        # Linux: write a freedesktop .desktop entry into the user applications
        # directory so Ghidra shows up in the app menu.
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop = app_dir / f"ghidra_{version}.desktop"

        # Convert Ghidra's bundled .ico to a .png the desktop entry can use.
        icon_path = dir_path / "support" / "ghidra_ico.png"
        _ico_to_png(ico_file_path, icon_path)

        entry = "[Desktop Entry]\n"
        entry += f"Name=Ghidra ({version})\n"
        entry += "Comment=Ghidra\n"
        entry += f"Exec={exec_cmd}\n"
        entry += f"Icon={icon_path}\n"
        entry += "Type=Application\n"
        entry += "Categories=Development\n"
        entry += "StartupWMClass=ghidra-Ghidra\n"
        desktop.write_text(entry, encoding="utf-8")
        launcher = desktop

    elif sys.platform == "darwin":
        # macOS: build a minimal .app bundle in /Applications whose executable
        # is a tiny shell script that re-enters GVM.
        base = Path("/Applications")
        name = f"Ghidra_{version}"
        app = base / f"{name}.app"
        app.mkdir(parents=True, exist_ok=True)

        bin_path = app / name
        # Quote the interpreter path and tag so spaces or shell metacharacters
        # in either can't break (or be injected into) the script.
        script = f"#!/bin/sh -i\nexec {shlex.quote(us)} -m gvm --launcher run {shlex.quote(tag)}\n"
        bin_path.write_text(script, encoding="utf-8")
        bin_path.chmod(0o744)

        # Bundles need a Contents/ with an Info.plist and a Resources/ icon.
        cont = app / "Contents"
        resource_dir = cont / "Resources"
        resource_dir.mkdir(parents=True, exist_ok=True)

        # Fill the plist template (shipped in res/) with this version's details.
        plist_template = (
            Path(__file__).parent.parent / "res" / "macos_plist.plist"
        ).read_text(encoding="utf-8")
        plist = plist_template.replace("{name}", name).replace("{version}", version)
        (cont / "Info.plist").write_text(plist, encoding="utf-8")

        _ico_to_png(ico_file_path, resource_dir / "Icon.png")
        launcher = app
    # NOTE: Windows intentionally has no desktop launcher yet (tracked in the
    # project's todo); `launcher` stays None and that's recorded in the cache.

    logger.info("📜 Regenerating config")
    # Bake the configured UI-scale override into this install's launch.properties.
    apply_ui_scale(dir_path, cacher.cache.prefs.ui_scale_override)

    # Record the freshly installed version (and its launcher) in the cache.
    cacher.cache.entries[tag] = CacheEntry(
        path=str(dir_path),
        launcher=str(launcher) if launcher else None,
        extensions={},
    )
    cacher.save()

    # The extracted directory is what we keep; the zip is no longer needed.
    dl_path.unlink()


def apply_ui_scale(install_dir: Path, scale: int) -> None:
    """Write the Java2D UI-scale override into an install's launch.properties.

    Ghidra reads its JVM args from ``support/launch.properties``. We keep a
    one-time pristine backup (``launch.properties.backup``) and always rebuild
    from it, so repeatedly changing the scale never stacks duplicate args.

    This is called both at install time and by the GUI when the user changes the
    scale and chooses to re-apply it to already-installed versions.
    """
    props_path = install_dir / "support" / "launch.properties"
    props_backup_path = install_dir / "support" / "launch.properties.backup"

    # Create the pristine backup the first time only; thereafter it's our source
    # of truth so edits are idempotent.
    if not props_backup_path.exists():
        shutil.copy2(props_path, props_backup_path)

    props = GhidraPropsFile.from_path(props_backup_path)
    vmargs = props.get_by_key("VMARGS_LINUX")
    if vmargs is None:
        raise RuntimeError("Can't find VMARGS_LINUX prop")
    # Drop any pre-existing uiScale arg, then append the requested value.
    vmargs = [v for v in vmargs if not v.startswith("-Dsun.java2d.uiScale=")]
    vmargs.append(f"-Dsun.java2d.uiScale={scale}")
    props.put("VMARGS_LINUX", vmargs)
    props.save_to_file(props_path)


def _ico_to_png(ico_path: Path, png_path: Path) -> None:
    """Convert a Windows .ico to a .png using Pillow.

    Used to give Linux/macOS launchers a usable icon. The ``with`` block ensures
    the source image file handle is closed promptly rather than relying on
    garbage collection.
    """
    from PIL import Image
    with Image.open(ico_path) as img:
        img.save(png_path, format="PNG")
