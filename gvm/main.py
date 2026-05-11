import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from gvm.cache import Cacher
from gvm.extensions import handle_ext_cmd
from gvm.install import install_version
from gvm.prefs_backup.backup_generator import BackupGenerator
from gvm.prefs_backup.backup_restorer import BackupRestorer

logger = logging.getLogger(__name__)

EXIT_CODE_NOT_FOUND = 1

_COMMAND_ALIASES = {
    "ls": "list",
    "i": "install",
    "r": "run",
    "del": "uninstall",
    "u": "update",
    "U": "check-update",
    "p": "prefs",
    "e": "extensions",
}
_SUB_ALIASES = {
    "ls": "list",
    "i": "install",
    "rm": "uninstall",
}


def _resolve_tag(tag: str | None, cacher: Cacher, default: str = "default") -> str:
    t = tag or default
    if t == "default":
        return cacher.default_explicit()
    if t == "latest":
        return cacher.cache.latest_known
    return t


def update_latest_version(cacher: Cacher) -> bool:
    resp = requests.get(
        "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest",
        headers={"User-Agent": "gvm"},
        timeout=30,
    )
    resp.raise_for_status()
    tag_name = resp.json()["tag_name"]

    if cacher.cache.latest_known != tag_name:
        logger.info("🔔🔔🔔 New version available: %s 🔔🔔🔔", tag_name)
        cacher.cache.latest_known = tag_name
        cacher.save()
        return True
    return False


def do_update_check(cacher: Cacher, args) -> bool:
    logger.debug("Checking for updates")
    try:
        new_version = update_latest_version(cacher)
    except Exception as e:
        logger.warning("Failed to check for update: %s", e)
        return False

    if new_version and getattr(args, "launcher", False):
        try:
            from plyer import notification
            notification.notify(title="New ghidra version available", app_icon="ghidra", timeout=5)
        except Exception:
            pass

    cacher.cache.last_update_check = datetime.now(timezone.utc)
    cacher.save()
    return new_version


def _allow_update_check(cmd: str) -> bool:
    return cmd not in ("locate", "list", "settings", "prefs")


def _backup_and_restore_prefs(
    cacher: Cacher, old_tag: str, new_tag: str, install_fn
) -> None:
    restorer = None
    if sys.platform != "win32" and old_tag and old_tag in cacher.cache.entries:
        logger.info("Backing up config from last launched version %s", old_tag)
        restorer = BackupGenerator.from_cached_version(
            cacher.cache.entries[old_tag], old_tag
        ).restorer()

    install_fn()

    if sys.platform != "win32" and restorer and new_tag in cacher.cache.entries:
        logger.info("Restoring config to %s", new_tag)
        restorer.restore_to_cached_version(cacher.cache.entries[new_tag])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gvm", description="Ghidra Version Manager")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable expanded logging")
    parser.add_argument("-o", "--offline", action="store_true", help="Disable network access")
    parser.add_argument("-l", "--launcher", action="store_true", help="Run in launcher mode")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", aliases=["ls"], help="List available Ghidra versions")

    p = sub.add_parser("install", aliases=["i"], help="Install a Ghidra version")
    p.add_argument("tag", help="Which version to install")

    p = sub.add_parser("run", aliases=["r"], help="Launch Ghidra")
    p.add_argument("-py", action="store_true", dest="pyghidra_once", help="Use PyGhidra for this launch only")
    p.add_argument("tag", nargs="?", default=None, help="Override the version to run")

    p = sub.add_parser("uninstall", aliases=["del"], help="Remove a Ghidra version")
    p.add_argument("tag", help="The version to remove")

    dp = sub.add_parser("default", help="Manage the default version")
    dsub = dp.add_subparsers(dest="default_cmd", required=True)
    dsub.add_parser("show", help="Display the current default Ghidra version")
    p = dsub.add_parser("set", help="Set the default version, installing it if needed")
    p.add_argument("tag")

    sub.add_parser("update", aliases=["u"], help="Update the default version")
    sub.add_parser("check-update", aliases=["U"], help="Force update check")

    pp = sub.add_parser("prefs", aliases=["p"], help="Manage preferences")
    psub = pp.add_subparsers(dest="prefs_cmd", required=True)
    psub.add_parser("show", help="Display current preferences")
    p = psub.add_parser("set", help="Set a preference")
    p.add_argument("key", help="Key to set (py3, scale, install_dir)")
    p.add_argument("value", help="New value")

    ep = sub.add_parser("extensions", aliases=["e"], help="Manage extensions")
    esub = ep.add_subparsers(dest="ext_cmd", required=True)
    esub.add_parser("list", aliases=["ls"], help="List known extensions")
    p = esub.add_parser("install", aliases=["i"], help="Install an extension")
    p.add_argument("name")
    p.add_argument("ghidra_version", nargs="?", default=None)
    p = esub.add_parser("uninstall", aliases=["rm"], help="Remove an extension")
    p.add_argument("name")
    p.add_argument("ghidra_version", nargs="?", default=None)

    sp = sub.add_parser("settings", help="Manage Ghidra settings")
    ssub = sp.add_subparsers(dest="settings_cmd", required=True)
    p = ssub.add_parser("backup", help="Export your current settings")
    p.add_argument("out", help="Destination file")
    p.add_argument("tag", nargs="?", default=None)
    p = ssub.add_parser("restore", help="Restore a prior backup")
    p.add_argument("src", help="Backup file")
    p.add_argument("tag", nargs="?", default=None)

    p = sub.add_parser("locate", help="Get the path to a given Ghidra version")
    p.add_argument("tag", nargs="?", default=None)

    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    home = Path.home()
    default_path = home / ".local" / "opt" / "gvm" if sys.platform != "win32" else home / "AppData" / "Local" / "gvm"
    default_path.mkdir(parents=True, exist_ok=True)

    cacher = Cacher.load(default_path / "cache.toml")

    # Use custom install directory if configured, otherwise use the default
    if cacher.cache.prefs.install_dir:
        path = Path(cacher.cache.prefs.install_dir)
        path.mkdir(parents=True, exist_ok=True)
    else:
        path = default_path

    cmd = _COMMAND_ALIASES.get(args.command, args.command)

    if _allow_update_check(cmd):
        now = datetime.now(timezone.utc)
        hours_since = (now - cacher.cache.last_update_check).total_seconds() / 3600
        if hours_since > 18 or not cacher.cache.latest_known:
            do_update_check(cacher, args)

    if cmd == "locate":
        tag = _resolve_tag(args.tag, cacher)
        if tag in cacher.cache.entries:
            print(cacher.cache.entries[tag].path)
        else:
            print("Not found", file=sys.stderr)
            sys.exit(EXIT_CODE_NOT_FOUND)

    elif cmd == "list":
        resp = requests.get(
            "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases",
            params={"per_page": 100},
            headers={"User-Agent": "gvm"},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json()
        logger.info("Available releases:")
        for c in results:
            if args.verbose:
                if c.get("name"):
                    logger.info("name: %s", c["name"])
                if c.get("created_at"):
                    logger.info("date: %s", c["created_at"])
                if c.get("assets"):
                    logger.info("URL: %s", c["assets"][0].get("url", ""))
                logger.info("--------")
            else:
                out = f"- {c['tag_name']}"
                if cacher.is_installed(c["tag_name"]):
                    out += " [installed]"
                if cacher.default_explicit() == c["tag_name"]:
                    out += " [default]"
                logger.info(out)

    elif cmd == "install":
        install_version(cacher, args, path, args.tag)

    elif cmd == "run":
        tag = _resolve_tag(args.tag, cacher)

        if not cacher.is_installed(tag):
            last = cacher.cache.last_launched
            _backup_and_restore_prefs(
                cacher, last, tag,
                lambda: install_version(cacher, args, path, tag),
            )

        cacher.cache.last_launched = tag
        cacher.save()

        entry = cacher.cache.entries[tag]
        install_path = Path(entry.path)

        use_pyghidra = args.pyghidra_once or cacher.cache.prefs.pyghidra
        if use_pyghidra:
            runner = install_path / ("support/pyghidraRun" if sys.platform != "win32" else "support/pyghidraRun.bat")
        elif sys.platform != "win32":
            runner = install_path / "ghidraRun"
        else:
            runner = install_path / "ghidraRun.bat"

        if not runner.exists():
            del cacher.cache.entries[tag]
            cacher.save()
            logger.error("Failed to find runner, did the installation get removed?")
            return

        logger.info("Launching %s", runner)
        if sys.platform == "linux":
            os.execv(str(runner), [str(runner)])
        else:
            subprocess.Popen([str(runner)])

    elif cmd == "uninstall":
        tag = _resolve_tag(args.tag, cacher)
        if tag in cacher.cache.entries:
            entry = cacher.cache.entries[tag]
            shutil.rmtree(entry.path, ignore_errors=True)
            if entry.launcher:
                lp = Path(entry.launcher)
                if lp.is_dir():
                    shutil.rmtree(lp, ignore_errors=True)
                elif lp.exists():
                    lp.unlink()
            del cacher.cache.entries[tag]
            cacher.save()
        else:
            logger.error("That version isn't installed")

    elif cmd == "default":
        dcmd = _SUB_ALIASES.get(args.default_cmd, args.default_cmd)
        if dcmd == "show":
            logger.info(cacher.cache.default)
        elif dcmd == "set":
            cacher.cache.default = args.tag
            cacher.save()
            if not cacher.is_installed(args.tag):
                install_version(cacher, args, path, args.tag)

    elif cmd == "update":
        if cacher.cache.default != "latest":
            logger.error("Can't update when default is a fixed version")
            return
        latest = cacher.cache.latest_known
        if cacher.is_installed(latest):
            logger.info("You have the latest version already!")
        else:
            last = cacher.cache.last_launched
            _backup_and_restore_prefs(
                cacher, last, latest,
                lambda: install_version(cacher, args, path, latest),
            )

    elif cmd == "check-update":
        cacher.cache.latest_known = ""
        if not do_update_check(cacher, args):
            logger.info("You have the latest version, I've checked")

    elif cmd == "prefs":
        pcmd = _SUB_ALIASES.get(args.prefs_cmd, args.prefs_cmd)
        if pcmd == "show":
            yn = "yes" if cacher.cache.prefs.pyghidra else "no"
            logger.info("Use PyGhidra in launchers? {py3} [%s]", yn)
            logger.info("Override ui scale {scale} [%d]", cacher.cache.prefs.ui_scale_override)
            install_display = cacher.cache.prefs.install_dir or str(default_path)
            is_custom = " (custom)" if cacher.cache.prefs.install_dir else " (default)"
            logger.info("Install directory {install_dir} [%s]%s", install_display, is_custom)
        elif pcmd == "set":
            if args.key == "py3":
                cacher.cache.prefs.pyghidra = args.value.lower() == "true"
                cacher.save()
            elif args.key == "scale":
                cacher.cache.prefs.ui_scale_override = int(args.value)
                cacher.save()
            elif args.key == "install_dir":
                if args.value.lower() == "default":
                    cacher.cache.prefs.install_dir = ""
                    cacher.save()
                    logger.info("Install directory reset to default: %s", default_path)
                else:
                    resolved = Path(args.value).resolve()
                    resolved.mkdir(parents=True, exist_ok=True)
                    cacher.cache.prefs.install_dir = str(resolved)
                    cacher.save()
                    logger.info("Install directory set to: %s", resolved)
            else:
                logger.error("Unknown key")

    elif cmd == "extensions":
        args.ext_cmd = _SUB_ALIASES.get(args.ext_cmd, args.ext_cmd)
        handle_ext_cmd(cacher, path, args)

    elif cmd == "settings":
        scmd = args.settings_cmd
        tag = _resolve_tag(args.tag, cacher)
        if scmd == "backup":
            if sys.platform == "win32":
                logger.error("This command is only supported on unix")
                return
            if tag in cacher.cache.entries:
                backup = BackupGenerator.from_cached_version(cacher.cache.entries[tag], tag)
                Path(args.out).write_bytes(backup.backup_data)
            else:
                logger.error("That version isn't installed")
        elif scmd == "restore":
            if sys.platform == "win32":
                logger.error("This command is only supported on unix")
                return
            if tag in cacher.cache.entries:
                BackupRestorer.from_path(Path(args.src)).restore_to_cached_version(
                    cacher.cache.entries[tag]
                )
            else:
                logger.error("That version isn't installed")


if __name__ == "__main__":
    main()
