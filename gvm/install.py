import logging
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


def do_java_check() -> None:
    try:
        result = subprocess.run(
            ["javac", "--version"], capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    logger.error("------------------------------")
    logger.error("You need to have the Java JDK (not JRE) installed to use Ghidra.")
    logger.error(
        "We tried to run `javac --version` but it failed, consider installing JDK "
        "(for Ghidra 11+ use version 21) LTS from the following:"
    )
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
    do_java_check()

    logger.debug("Installing tag '%s'", tag)
    if tag in cacher.cache.entries:
        logger.info("That version is already installed")
        return

    if tag == "default":
        tag = cacher.default_explicit()
    elif tag == "latest":
        tag = cacher.cache.latest_known
    logger.debug("Installing actual tag '%s'", tag)

    resp = requests.get(
        f"https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/tags/{tag}",
        headers={"User-Agent": "gvm"},
    )
    resp.raise_for_status()
    release = resp.json()

    assets = release.get("assets", [])
    if not assets:
        raise RuntimeError("This tag doesn't have an asset attached")
    asset = assets[0]
    url = asset["browser_download_url"]
    asset_size = asset.get("size", 0)

    logger.info("⬇️  Downloading: %s", url)

    dl_path = path / f"ghidra_{release['tag_name']}.zip"
    logger.info("💾 Saving to %s", dl_path)

    if dl_path.exists() and __debug__:
        logger.info("Using cached download")
    elif not getattr(args, "offline", False):
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
        logger.error("Offline and no cached version found")
        return

    logger.info("📦 Extracting to %s", path)

    try:
        with zipfile.ZipFile(dl_path, "r") as zf:
            zf.extractall(path)
    except zipfile.BadZipFile as e:
        dl_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not open zip file, deleting: {e}") from e
    except Exception as e:
        dl_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not extract zip file, deleting: {e}") from e

    logger.info("⚙️  Creating application launcher entries")

    file_name = dl_path.name
    parts = file_name.split("_")
    version = parts[2]
    dir_name = f"ghidra_{version}_PUBLIC"

    dir_path = dl_path.parent / dir_name
    if not dir_path.exists():
        logger.info("Failed to find extract, trying old style without suffix")
        dir_path = dl_path.parent / f"ghidra_{version}"

    us = sys.executable
    exec_cmd = f"{us} -m gvm --launcher run {tag}"

    ico_file_path = dir_path / "support" / "ghidra.ico"

    launcher: Path | None = None

    if sys.platform == "linux":
        app_dir = Path.home() / ".local" / "share" / "applications"
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop = app_dir / f"ghidra_{version}.desktop"

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
        base = Path("/Applications")
        name = f"Ghidra_{version}"
        app = base / f"{name}.app"
        app.mkdir(parents=True, exist_ok=True)

        bin_path = app / name
        script = f"#!/bin/sh -i\n{exec_cmd}\n"
        bin_path.write_text(script, encoding="utf-8")
        bin_path.chmod(0o744)

        cont = app / "Contents"
        resource_dir = cont / "Resources"
        resource_dir.mkdir(parents=True, exist_ok=True)

        plist_template = (
            Path(__file__).parent.parent / "res" / "macos_plist.plist"
        ).read_text(encoding="utf-8")
        plist = plist_template.replace("{name}", name).replace("{version}", version)
        (cont / "Info.plist").write_text(plist, encoding="utf-8")

        _ico_to_png(ico_file_path, resource_dir / "Icon.png")
        launcher = app

    logger.info("📜 Regenerating config")
    props_path = dir_path / "support" / "launch.properties"
    props_backup_path = dir_path / "support" / "launch.properties.backup"
    shutil.copy2(props_path, props_backup_path)

    props = GhidraPropsFile.from_path(props_backup_path)
    vmargs = props.get_by_key("VMARGS_LINUX")
    if vmargs is None:
        raise RuntimeError("Can't find VMARGS_LINUX prop")
    vmargs = [v for v in vmargs if not v.startswith("-Dsun.java2d.uiScale=")]
    vmargs.append(f"-Dsun.java2d.uiScale={cacher.cache.prefs.ui_scale_override}")
    props.put("VMARGS_LINUX", vmargs)
    props.save_to_file(props_path)

    cacher.cache.entries[tag] = CacheEntry(
        path=str(dir_path),
        launcher=str(launcher) if launcher else None,
        extensions={},
    )
    cacher.save()

    dl_path.unlink()


def _ico_to_png(ico_path: Path, png_path: Path) -> None:
    from PIL import Image
    img = Image.open(ico_path)
    img.save(png_path, format="PNG")
