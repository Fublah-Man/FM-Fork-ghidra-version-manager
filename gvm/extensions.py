import gzip
import logging
import shutil
import tarfile
from pathlib import Path

import requests
import tomllib

from gvm.cache import Cacher, ExtEntry

logger = logging.getLogger(__name__)

EXTENSIONS_REPO = Path(__file__).parent.parent / "extensions-repo"


def _load_all_extensions() -> list[dict]:
    exts = []
    for p in sorted(EXTENSIONS_REPO.glob("*.toml")):
        with open(p, "rb") as f:
            exts.append(tomllib.load(f))
    return exts


def find_by_name(name: str) -> dict:
    for ext in _load_all_extensions():
        if ext["name"].lower() == name.lower():
            return ext
    raise ValueError(f"Failed to find {name}")


def handle_ext_cmd(cacher: Cacher, path: Path, args) -> None:
    cmd = args.ext_cmd

    if cmd in ("list", "ls"):
        logger.info("Known extensions:")
        for ext in _load_all_extensions():
            logger.info("- %s", ext["name"])

    elif cmd in ("install", "i"):
        _ext_install(cacher, path, args)

    elif cmd in ("uninstall", "rm"):
        _ext_uninstall(cacher, args)


def _ext_install(cacher: Cacher, path: Path, args) -> None:
    ghidra_version = getattr(args, "ghidra_version", None) or cacher.default_explicit()

    if not cacher.is_installed(ghidra_version):
        logger.error("Version '%s' isn't installed!", ghidra_version)
        return

    try:
        entry = find_by_name(args.name)
    except ValueError as e:
        logger.error("%s", e)
        return

    ghidra_ent = cacher.cache.entries[ghidra_version]
    if entry["slug"] in ghidra_ent.extensions:
        logger.error("That extension is already installed")
        return

    kind = entry.get("kind", "DownloadOnly")

    if kind == "DownloadOnly":
        _install_download_only(cacher, path, entry, ghidra_version)
    elif kind == "ProcessorGit":
        _install_processor_git(cacher, path, entry, ghidra_version)


def _install_download_only(
    cacher: Cacher, path: Path, entry: dict, ghidra_version: str
) -> None:
    logger.info("Installing download only extension")

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
    with open(dl_path, "wb") as f, tqdm(total=asset_size, unit="B", unit_scale=True) as pbar:
        for chunk in dl_resp.iter_content(chunk_size=65536):
            f.write(chunk)
            pbar.update(len(chunk))

    logger.info(
        "This extension requires manual installation, please install using "
        "File->Install Extensions and select:"
    )
    logger.info("%s", dl_path)

    cacher.cache.entries[ghidra_version].extensions[entry["slug"]] = ExtEntry(
        files=[str(dl_path)]
    )
    cacher.save()


def _install_processor_git(
    cacher: Cacher, path: Path, entry: dict, ghidra_version: str
) -> None:
    logger.info("Installing git processor extension")

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
    with open(dl_path, "wb") as f, tqdm(unit="B", unit_scale=True) as pbar:
        for chunk in dl_resp.iter_content(chunk_size=65536):
            f.write(chunk)
            pbar.update(len(chunk))

    logger.info("Download done")

    cache_ent = cacher.cache.entries.get(ghidra_version)
    if cache_ent is None:
        raise RuntimeError(f"Version {ghidra_version} isn't known")

    base = Path(cache_ent.path) / "Ghidra" / "Processors"
    ext_entry = ExtEntry(files=[str(base / entry["name"])])
    logger.info("files: %s", ext_entry.files)

    no_prefix = entry.get("no_prefix", False)
    ext_name = entry["name"]

    with gzip.open(dl_path, "rb") as gz_f:
        with tarfile.open(fileobj=gz_f, mode="r|") as tar:
            tmp_prefix = ""
            members_to_extract: list[tuple[tarfile.TarInfo, bytes]] = []

            for member in tar:
                member_path = member.name

                if not tmp_prefix:
                    if no_prefix:
                        if member.isdir():
                            tmp_prefix = member_path.rstrip("/") + "/"
                    else:
                        if member_path.endswith(f"/{ext_name}/"):
                            tmp_prefix = member_path
                    continue

                if not member.isfile():
                    continue
                if not member_path.startswith(tmp_prefix):
                    continue

                rel = member_path[len(tmp_prefix):]
                out_path = base / ext_name / rel
                out_path.parent.mkdir(parents=True, exist_ok=True)

                f_obj = tar.extractfile(member)
                if f_obj is not None:
                    out_path.write_bytes(f_obj.read())
                    logger.info("%s", out_path)
                    ext_entry.files.append(str(out_path))

    cacher.cache.entries[ghidra_version].extensions[entry["slug"]] = ext_entry
    cacher.save()


def _ext_uninstall(cacher: Cacher, args) -> None:
    ghidra_version = getattr(args, "ghidra_version", None) or cacher.default_explicit()

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
