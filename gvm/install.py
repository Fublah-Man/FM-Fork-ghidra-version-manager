"""Download, extract and register a Ghidra release.

This module owns the "install" side of GVM: it checks that a JDK is present,
fetches the release metadata from the GitHub API, downloads the release zip,
verifies and extracts it, creates a platform-appropriate desktop launcher,
patches Ghidra's ``launch.properties`` (for the UI-scale override), and finally
records everything in the cache.
"""

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

from gvm import http as ghttp
from gvm.cache import CacheEntry, Cacher
from gvm.ghidra_props_parser import GhidraPropsFile

logger = logging.getLogger(__name__)

# Upper bound on a single download, as a safety net against an unbounded or
# malicious payload. Ghidra releases are a few hundred MB; 4 GiB is well clear.
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024

# Hosts we are willing to download release assets from. The asset URL comes out
# of the GitHub API response, which is attacker-controlled under a MITM or
# compromised-upstream threat model; without this check a tampered response
# could point the download at any server.
ALLOWED_DOWNLOAD_HOSTS = frozenset({
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
})

# A release version string is interpolated into filesystem paths (the extracted
# directory name, the Linux .desktop filename, the macOS .app bundle name). It
# is derived from the release tag, which is server-controlled, so it must be a
# plain version-like token: no separators, no "..", no shell metacharacters.
_SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _read_package_resource(relative: str) -> str:
    """Read a text file shipped as package data inside ``gvm``."""
    try:
        from importlib.resources import files
        return (files("gvm") / relative).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except Exception:  # pragma: no cover - unusual loaders only
        return (Path(__file__).parent / relative).read_text(encoding="utf-8")


def _validate_version(version: str) -> str:
    """Reject a version string that isn't safe to put in a filesystem path.

    Guards the launcher-generation code below. ``version`` is pulled out of the
    release zip's filename, which derives from the release tag; a tag shaped
    ``a_<payload>_b`` places ``<payload>`` here verbatim. Without this check a
    payload of ``../../../..`` escaped the applications directory entirely and
    let a crafted release write a .desktop file (with an attacker-chosen
    ``Exec=`` line) anywhere the user could write — ``~/.config/autostart/``
    being the obvious target.
    """
    if not _SAFE_VERSION_RE.match(version):
        raise RuntimeError(
            f"Refusing to use unsafe version string from release tag: {version!r}. "
            "Expected a plain version like '11.4'."
        )
    return version


def _check_download_host(url: str) -> None:
    """Abort unless *url* is https on a known GitHub host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS download URL: {url}")
    if parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise RuntimeError(
            f"Refusing download from unexpected host {parsed.hostname!r}. "
            "Release assets must come from GitHub."
        )


def _verify_digest(file_path: Path, asset: dict, require: bool = False) -> None:
    """Integrity check of a downloaded asset.

    Newer GitHub API responses include a ``digest`` field on each asset of the
    form ``"sha256:<hex>"``. When present we recompute the SHA-256 of the file
    we just downloaded and abort if it doesn't match — this catches truncated
    downloads and tampering in transit.

    When the API omits ``digest`` there is nothing to compare against. That used
    to be skipped at *debug* level, which meant a network attacker able to alter
    the response could simply strip the field and silently downgrade the install
    to unverified. It is now a visible warning, and ``require=True`` (from
    ``--require-digest``) turns it into a hard failure.
    """
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        name = asset.get("name", file_path.name)
        if require:
            file_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"No SHA-256 digest published for {name} and --require-digest "
                "was given; refusing to install an unverified download."
            )
        logger.warning(
            "No SHA-256 digest published for %s — integrity could NOT be "
            "verified. Pass --require-digest to refuse unverified downloads.",
            name,
        )
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


def _select_release_zip(assets: list[dict]) -> dict:
    """Pick the Ghidra release zip from a release's asset list.

    Prefers the first asset whose name ends in ``.zip`` over a blind
    ``assets[0]``: GitHub does not guarantee asset ordering, and a release that
    gains a checksum or signature asset would otherwise break installs.
    """
    for a in assets:
        if str(a.get("name", "")).lower().endswith(".zip"):
            return a
    logger.warning("No .zip asset found in release; falling back to the first asset")
    return assets[0]


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

    # Installing needs the release metadata, which requires the network. Check
    # this before the JDK probe so `--offline install` reports the actual
    # blocker instead of printing the whole JDK install guide first.
    if getattr(args, "offline", False):
        logger.error("Can't install %s while offline (need release metadata)", tag)
        return

    # Nudge the user about the JDK requirement before we do any heavy work.
    do_java_check()

    # Fetch the release metadata for this exact tag from GitHub.
    try:
        resp = ghttp.get(
            f"https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/tags/{tag}",
            timeout=30,
        )
        resp.raise_for_status()
    except requests.HTTPError:
        logger.error("No such Ghidra release: %s", tag)
        return
    except requests.RequestException as e:
        logger.error("Couldn't reach GitHub to fetch release %s: %s", tag, e)
        return
    release = resp.json()

    # Ghidra publishes a single zip asset per release. Prefer an actual .zip
    # over a blind assets[0] — releases sometimes carry extra assets (checksum
    # or signature files) and their order is not guaranteed.
    assets = release.get("assets", [])
    if not assets:
        raise RuntimeError("This tag doesn't have an asset attached")
    asset = _select_release_zip(assets)
    url = asset["browser_download_url"]
    asset_size = asset.get("size", 0)

    # The URL comes from the API response; confirm it still points at GitHub
    # before we stream anything from it.
    _check_download_host(url)

    logger.info("Downloading: %s", url)

    # Save the download next to the install dir, named after the *real* tag.
    dl_path = path / f"ghidra_{release['tag_name']}.zip"
    logger.info("Saving to %s", dl_path)

    # Reusing an existing zip is a development convenience. It used to be gated
    # on ``__debug__``, which is True in every normal run — so it was in fact the
    # default for all users, and a stale or planted file in a shared directory
    # would be used in place of a fresh download. It now requires an explicit
    # opt-in.
    reuse_cached = bool(getattr(args, "use_cached", False)) or os.environ.get("GVM_USE_CACHED_DOWNLOAD") == "1"

    if dl_path.exists() and reuse_cached:
        logger.info("Using cached download (explicitly enabled)")
    elif not getattr(args, "offline", False):
        # Stream the download to disk with a progress bar sized to the asset,
        # bounding total bytes as a safety net and cleaning up a partial file.
        try:
            dl_resp = ghttp.get(url, stream=True, timeout=300)
            dl_resp.raise_for_status()
        except requests.RequestException as e:
            # Previously unguarded, unlike the metadata fetch above, so a dropped
            # connection surfaced as a raw traceback.
            logger.error("Failed to download %s: %s", url, e)
            return
        written = 0
        try:
            with (
                open(dl_path, "wb") as f,
                tqdm(total=asset_size, unit="B", unit_scale=True) as pbar,
            ):
                for chunk in dl_resp.iter_content(chunk_size=65536):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError(
                            f"Download exceeded {MAX_DOWNLOAD_BYTES} bytes; aborting"
                        )
                    f.write(chunk)
                    pbar.update(len(chunk))
        except Exception:
            dl_path.unlink(missing_ok=True)
            raise
    else:
        # --offline was passed and we have no cached copy: can't continue.
        logger.error("Offline and no cached version found")
        return

    # Verify the download's integrity before trusting its contents.
    _verify_digest(dl_path, asset, require=bool(getattr(args, "require_digest", False)))

    logger.info("Extracting to %s", path)

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

    logger.info("Creating application launcher entries")

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
    # Checking the segment *count* is not enough: a tag shaped "a_<payload>_b"
    # puts <payload> straight into `version`, which is then interpolated into
    # several filesystem paths below. Validate the content too.
    version = _validate_version(parts[2])
    dir_name = f"ghidra_{version}_PUBLIC"

    # Ghidra historically used a "_PUBLIC" suffix on the extracted folder; fall
    # back to the un-suffixed name for older releases.
    dir_path = dl_path.parent / dir_name
    if not dir_path.exists():
        logger.info("Failed to find extract, trying old style without suffix")
        dir_path = dl_path.parent / f"ghidra_{version}"

    # If neither layout is present the extraction produced something we don't
    # understand. Fail here with a clear message rather than letting every
    # downstream step (launcher generation, apply_ui_scale) fail obscurely on a
    # path that doesn't exist.
    if not dir_path.exists():
        raise RuntimeError(
            f"Extracted archive did not contain the expected directory "
            f"'{dir_name}' (or 'ghidra_{version}') under {dl_path.parent}"
        )

    # The launcher will invoke this Python interpreter to re-enter GVM in
    # "launcher" mode and run the chosen version. Every interpolated value is
    # shell-quoted: this string becomes a .desktop Exec= line (Linux) or a shell
    # script body (macOS), both of which are parsed as a command line.
    us = sys.executable
    exec_cmd = f"{shlex.quote(us)} -m gvm --launcher run {shlex.quote(tag)}"

    ico_file_path = dir_path / "support" / "ghidra.ico"

    launcher: Path | None = None

    if sys.platform == "linux":
        # Linux: write a freedesktop .desktop entry into the user applications
        # directory so Ghidra shows up in the app menu.
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop = app_dir / f"ghidra_{version}.desktop"

        # Convert Ghidra's bundled .ico to a .png the desktop entry can use.
        # The icon is cosmetic — if Pillow is missing or the .ico isn't there,
        # warn and carry on rather than aborting (and orphaning) the install.
        icon_path = dir_path / "support" / "ghidra_ico.png"
        icon_line = ""
        try:
            _ico_to_png(ico_file_path, icon_path)
            icon_line = f"Icon={icon_path}\n"
        except Exception as e:
            logger.warning("Couldn't generate launcher icon: %s", e)

        entry = "[Desktop Entry]\n"
        entry += f"Name=Ghidra ({version})\n"
        entry += "Comment=Ghidra\n"
        entry += f"Exec={exec_cmd}\n"
        entry += icon_line
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
        # exec_cmd is already fully shell-quoted (see above).
        script = f"#!/bin/sh -i\nexec {exec_cmd}\n"
        bin_path.write_text(script, encoding="utf-8")
        bin_path.chmod(0o744)

        # Bundles need a Contents/ with an Info.plist and a Resources/ icon.
        cont = app / "Contents"
        resource_dir = cont / "Resources"
        resource_dir.mkdir(parents=True, exist_ok=True)

        # Fill the plist template (shipped as package data in gvm/res/) with
        # this version's details. Resolved via importlib.resources because the
        # old repo-root path was absent from every non-editable install, making
        # this an unhandled FileNotFoundError *after* Ghidra had already been
        # extracted and the .app directory created — an orphaned half-install.
        plist_template = _read_package_resource("res/macos_plist.plist")
        plist = plist_template.replace("{name}", name).replace("{version}", version)
        (cont / "Info.plist").write_text(plist, encoding="utf-8")

        # Cosmetic icon — non-fatal if it can't be produced.
        try:
            _ico_to_png(ico_file_path, resource_dir / "Icon.png")
        except Exception as e:
            logger.warning("Couldn't generate app icon: %s", e)
        launcher = app
    # NOTE: Windows intentionally has no desktop launcher yet (tracked in the
    # project's todo); `launcher` stays None and that's recorded in the cache.

    logger.info("Regenerating config")
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
    dl_path.unlink(missing_ok=True)


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

    # An unusual release layout (or a partially-extracted install) leaves this
    # file missing. Warn and skip rather than raising an unguarded
    # FileNotFoundError out of shutil.copy2 — the UI-scale override is a nicety,
    # not a reason to abort an otherwise-good install.
    if not props_path.is_file() and not props_backup_path.is_file():
        logger.warning(
            "No launch.properties at %s; skipped UI-scale override", props_path
        )
        return

    # Create the pristine backup the first time only; thereafter it's our source
    # of truth so edits are idempotent.
    if not props_backup_path.exists():
        shutil.copy2(props_path, props_backup_path)

    props = GhidraPropsFile.from_path(props_backup_path)

    # Ghidra keeps separate VM-arg lines per OS; patch whichever ones this
    # launch.properties actually defines so the override works on Linux, Windows
    # and macOS (not just Linux).
    patched_any = False
    for key in ("VMARGS_LINUX", "VMARGS_WIN", "VMARGS_MACOS", "VMARGS"):
        vmargs = props.get_by_key(key)
        if vmargs is None:
            continue
        # Drop any pre-existing uiScale arg, then append the requested value.
        vmargs = [v for v in vmargs if not v.startswith("-Dsun.java2d.uiScale=")]
        vmargs.append(f"-Dsun.java2d.uiScale={scale}")
        props.put(key, vmargs)
        patched_any = True

    if not patched_any:
        # Nothing to patch — warn rather than raise so a single unusual release
        # doesn't abort the whole install.
        logger.warning("No VMARGS_* key in %s; skipped UI-scale override", props_path)
        return
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
