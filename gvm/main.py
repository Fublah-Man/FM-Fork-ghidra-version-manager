"""Command-line entry point for GVM.

This module wires up the ``gvm`` CLI: it defines the argument parser, performs
a rate-limited "is there a new Ghidra release?" check, and dispatches each
sub-command (list / install / run / uninstall / default / update / prefs /
extensions / settings / locate / gui) to the appropriate handler.
"""

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

# Process exit code used when a `locate` lookup fails, so scripts can detect it.
EXIT_CODE_NOT_FOUND = 1

# Short aliases for top-level commands (e.g. `gvm i` == `gvm install`). These are
# resolved manually after parsing because argparse aliases on the top-level
# subparser don't compose cleanly with our dispatch table.
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
# Aliases for second-level sub-commands shared across groups (extensions, prefs).
_SUB_ALIASES = {
    "ls": "list",
    "i": "install",
    "rm": "uninstall",
}


def _resolve_tag(tag: str | None, cacher: Cacher, default: str = "default") -> str:
    """Turn a possibly-None / sentinel tag into a concrete version string.

    Resolution order:
      * None              -> *default* (usually "default")
      * "default"         -> the configured default version
      * "latest"          -> the last-known latest release

    NOTE: this can legitimately return an empty string when "latest"/"default"
    resolve to ``latest_known`` and no update check has succeeded yet. Callers
    that turn the result into a download or a cache lookup must handle an empty
    value (the `run`/`update`/`install` paths below all guard for it); read-only
    callers like `locate` treat "" as simply "not found", which is fine.
    """
    t = tag or default
    if t == "default":
        return cacher.default_explicit()
    if t == "latest":
        return cacher.cache.latest_known
    return t


def update_latest_version(cacher: Cacher) -> bool:
    """Query GitHub for the newest release and record it.

    Returns True if this call discovered a *newer* tag than we had cached
    (i.e. an update just became available), False otherwise.
    """
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
    """Run an update check, swallowing network errors so the CLI stays usable.

    Returns whether a new version was found. Records the check time regardless
    so we don't hammer the API on every invocation.
    """
    logger.debug("Checking for updates")
    try:
        new_version = update_latest_version(cacher)
    except requests.RequestException as e:
        # Network/HTTP problems are expected (offline, rate-limited, ...) and
        # should never abort the user's actual command. Catch them narrowly so
        # genuine bugs (e.g. a KeyError) still surface.
        logger.warning("Failed to check for update: %s", e)
        return False

    # In launcher mode, pop a desktop notification when something new appears.
    if new_version and getattr(args, "launcher", False):
        try:
            from plyer import notification
<<<<<<< Updated upstream
            notification.notify(title="New ghidra version available", app_icon="ghidra", timeout=5)
        except ImportError:
            # plyer is an optional extra; absence just means "no notifications".
            logger.debug("plyer not installed; skipping desktop notification")
        except Exception as e:
            # Any other notification backend failure is non-fatal.
            logger.debug("Failed to send notification: %s", e)
=======
            notify = getattr(notification, "notify", None)
            if callable(notify):
                notify(title="New ghidra version available", app_icon="ghidra", timeout=5)
        except Exception:
            pass
>>>>>>> Stashed changes

    cacher.cache.last_update_check = datetime.now(timezone.utc)
    cacher.save()
    return new_version


<<<<<<< Updated upstream
def _allow_update_check(cmd: str) -> bool:
    # Skip the implicit update check for commands that are purely local/offline
    # or where a network stall would be annoying (locate, list, settings, ...).
=======
def _allow_update_check(cmd: str | None) -> bool:
>>>>>>> Stashed changes
    return cmd not in ("locate", "list", "settings", "prefs", "gui")


def _backup_and_restore_prefs(
    cacher: Cacher, old_tag: str, new_tag: str, install_fn
) -> None:
    """Carry a user's preferences across a version switch.

    Before installing *new_tag*, snapshot the preferences of the previously
    launched version (*old_tag*); after the install succeeds, restore that
    snapshot into the new version. This keeps settings like key bindings when
    the user updates Ghidra.
    """
    restorer = None
    if old_tag and old_tag in cacher.cache.entries:
        try:
            logger.info("Backing up config from last launched version %s", old_tag)
            restorer = BackupGenerator.from_cached_version(
                cacher.cache.entries[old_tag], old_tag
            ).restorer()
        except FileNotFoundError:
            # The old version was never actually launched, so it has no prefs
            # file to migrate — that's fine, just skip the backup.
            logger.debug("No preferences found for %s, skipping backup", old_tag)

    # Perform the actual install (passed in as a closure by the caller).
    install_fn()

    if restorer and new_tag in cacher.cache.entries:
        logger.info("Restoring config to %s", new_tag)
        restorer.restore_to_cached_version(cacher.cache.entries[new_tag])


def build_parser() -> argparse.ArgumentParser:
    """Construct the full argparse command tree."""
    parser = argparse.ArgumentParser(prog="gvm", description="Ghidra Version Manager")
    # Global flags available before any sub-command.
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
    p.add_argument("key", help="Key to set (py3, scale, install_dir, ext_dir)")
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
    p = esub.add_parser("scan", help="Scan the extensions directory and add found extensions")
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

    sub.add_parser("gui", help="Launch the graphical interface")

    return parser


def main() -> None:
    # Default logging: INFO level with a terse "LEVEL message" format.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = build_parser()
    args = parser.parse_args()

    # -v bumps the root logger to DEBUG for the rest of the run.
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # GVM's own data directory (cache + default install root). Linux/macOS use
    # ~/.local/opt/gvm; Windows uses %LOCALAPPDATA%\gvm.
    home = Path.home()
    default_path = home / ".local" / "opt" / "gvm" if sys.platform != "win32" else home / "AppData" / "Local" / "gvm"
    default_path.mkdir(parents=True, exist_ok=True)

    cacher = Cacher.load(default_path / "cache.toml")

    # Use a custom install directory if the user configured one, else default.
    if cacher.cache.prefs.install_dir:
        path = Path(cacher.cache.prefs.install_dir)
        path.mkdir(parents=True, exist_ok=True)
    else:
        path = default_path

    # Map any top-level alias (e.g. "i") to its canonical command name.
    cmd = _COMMAND_ALIASES.get(args.command, args.command)

    # The GUI is launched in a separate module; hand off immediately so we don't
    # run the CLI-oriented update check below.
    if cmd == "gui":
        from gvm.gui import launch_gui
        launch_gui()
        return

    # Implicit, rate-limited update check: at most once every 18 hours, and
    # always if we've never learned a latest version. Skipped for offline-y
    # commands (see _allow_update_check).
    if _allow_update_check(cmd):
        now = datetime.now(timezone.utc)
        hours_since = (now - cacher.cache.last_update_check).total_seconds() / 3600
        if hours_since > 18 or not cacher.cache.latest_known:
            do_update_check(cacher, args)

    if cmd == "locate":
        # Print the on-disk path for a version, or fail with a distinct code.
        tag = _resolve_tag(args.tag, cacher)
        if tag in cacher.cache.entries:
            print(cacher.cache.entries[tag].path)
        else:
            print("Not found", file=sys.stderr)
            sys.exit(EXIT_CODE_NOT_FOUND)

    elif cmd == "list":
        # Show up to 100 releases from GitHub, annotating installed/default.
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
                # Verbose mode dumps extra metadata per release.
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

        # Guard the empty-tag case (no version given and latest unknown) before
        # we try to index the cache below, which would KeyError.
        if not tag:
            logger.error(
                "No version to run: none specified and the latest version isn't "
                "known yet. Install a version or run `gvm check-update` first."
            )
            return

        # If the requested version isn't installed, install it first — migrating
        # preferences from whatever was launched last.
        if not cacher.is_installed(tag):
            last = cacher.cache.last_launched
            _backup_and_restore_prefs(
                cacher, last, tag,
                lambda: install_version(cacher, args, path, tag),
            )

        # The install may have bailed (e.g. empty/unknown tag); confirm before use.
        if tag not in cacher.cache.entries:
            logger.error("Version %s is not installed", tag)
            return

        # Remember this as the most recently launched version (for prefs migration).
        cacher.cache.last_launched = tag
        cacher.save()

        entry = cacher.cache.entries[tag]
        install_path = Path(entry.path)

        # Choose the runner script: PyGhidra vs plain, and the OS-specific name.
        use_pyghidra = args.pyghidra_once or cacher.cache.prefs.pyghidra
        if use_pyghidra:
            runner = install_path / ("support/pyghidraRun" if sys.platform != "win32" else "support/pyghidraRun.bat")
        elif sys.platform != "win32":
            runner = install_path / "ghidraRun"
        else:
            runner = install_path / "ghidraRun.bat"

        if not runner.exists():
            # The install directory was removed out from under us; drop the stale
            # cache entry so the next run re-installs cleanly.
            del cacher.cache.entries[tag]
            cacher.save()
            logger.error("Failed to find runner, did the installation get removed?")
            return

        logger.info("Launching %s", runner)
        if sys.platform == "linux":
            # On Linux, replace this process with Ghidra so no idle Python lingers.
            os.execv(str(runner), [str(runner)])
        else:
            # execv on Windows/macOS behaves differently (and .bat isn't directly
            # exec-able on Windows), so spawn a child and exit so we don't leave
            # GVM sitting in the foreground waiting on Ghidra.
            subprocess.Popen([str(runner)])
            sys.exit(0)

    elif cmd == "uninstall":
        tag = _resolve_tag(args.tag, cacher)
        if tag in cacher.cache.entries:
            entry = cacher.cache.entries[tag]
            # Remove the unpacked Ghidra tree...
            shutil.rmtree(entry.path, ignore_errors=True)
            # ...and the launcher we created (a dir on macOS, a file on Linux).
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
            # Record the new default, installing it if we don't already have it.
            cacher.cache.default = args.tag
            cacher.save()
            if not cacher.is_installed(args.tag):
                install_version(cacher, args, path, args.tag)

    elif cmd == "update":
        # "update" only makes sense when tracking "latest"; a pinned default
        # has nothing to update to.
        if cacher.cache.default != "latest":
            logger.error("Can't update when default is a fixed version")
            return
        latest = cacher.cache.latest_known
        # Guard the unknown-latest case so we don't try to install an empty tag.
        if not latest:
            logger.error(
                "Latest version isn't known yet. Run `gvm check-update` first."
            )
            return
        if cacher.is_installed(latest):
            logger.info("You have the latest version already!")
        else:
            # Install the new version, carrying prefs over from the last launch.
            last = cacher.cache.last_launched
            _backup_and_restore_prefs(
                cacher, last, latest,
                lambda: install_version(cacher, args, path, latest),
            )

    elif cmd == "check-update":
        # Force a check by clearing the cached "latest" first so the comparison
        # in update_latest_version always reports the current newest as "new".
        cacher.cache.latest_known = ""
        if not do_update_check(cacher, args):
            logger.info("You have the latest version, I've checked")

    elif cmd == "prefs":
        pcmd = _SUB_ALIASES.get(args.prefs_cmd, args.prefs_cmd)
        if pcmd == "show":
            # Render each preference; the literal "{key}" tokens mirror the keys
            # accepted by `prefs set` so users can see what to type.
            yn = "yes" if cacher.cache.prefs.pyghidra else "no"
            logger.info("Use PyGhidra in launchers? {py3} [%s]", yn)
            logger.info("Override ui scale {scale} [%d]", cacher.cache.prefs.ui_scale_override)
            install_display = cacher.cache.prefs.install_dir or str(default_path)
            is_custom = " (custom)" if cacher.cache.prefs.install_dir else " (default)"
            logger.info("Install directory {install_dir} [%s]%s", install_display, is_custom)
            ext_display = cacher.cache.prefs.ext_dir or "not set"
            logger.info("Extensions directory {ext_dir} [%s]", ext_display)
        elif pcmd == "set":
            if args.key == "py3":
                # Any value other than "true" (case-insensitive) means False.
                cacher.cache.prefs.pyghidra = args.value.lower() == "true"
                cacher.save()
            elif args.key == "scale":
                # Validate the scale: it must be a positive integer, and we cap
                # it at a sane upper bound so a typo can't make Ghidra unusable.
                try:
                    scale = int(args.value)
                except ValueError:
                    logger.error("UI scale must be an integer, got: %s", args.value)
                    return
                if scale < 1 or scale > 16:
                    logger.error("UI scale must be between 1 and 16, got: %d", scale)
                    return
                cacher.cache.prefs.ui_scale_override = scale
                cacher.save()
            elif args.key == "install_dir":
                if args.value.lower() == "default":
                    # Reset to the built-in default location.
                    cacher.cache.prefs.install_dir = ""
                    cacher.save()
                    logger.info("Install directory reset to default: %s", default_path)
                else:
                    # Resolve to an absolute path and create it if needed.
                    resolved = Path(args.value).resolve()
                    resolved.mkdir(parents=True, exist_ok=True)
                    cacher.cache.prefs.install_dir = str(resolved)
                    cacher.save()
                    logger.info("Install directory set to: %s", resolved)
            elif args.key == "ext_dir":
                if args.value.lower() == "default":
                    # "default" clears the extensions directory (there is none
                    # by default).
                    cacher.cache.prefs.ext_dir = ""
                    cacher.save()
                    logger.info("Extensions directory cleared")
                else:
                    # The extensions dir must already exist (we scan it, we don't
                    # create it).
                    resolved = Path(args.value).resolve()
                    if not resolved.is_dir():
                        logger.error("Directory does not exist: %s", resolved)
                        return
                    cacher.cache.prefs.ext_dir = str(resolved)
                    cacher.save()
                    logger.info("Extensions directory set to: %s", resolved)
            else:
                logger.error("Unknown key")

    elif cmd == "extensions":
        # Normalise the extension sub-command alias, then hand off.
        args.ext_cmd = _SUB_ALIASES.get(args.ext_cmd, args.ext_cmd)
        handle_ext_cmd(cacher, path, args)

    elif cmd == "settings":
        scmd = args.settings_cmd
        tag = _resolve_tag(args.tag, cacher)
        if scmd == "backup":
            # Write a ZIP backup of the chosen version's preferences.
            if tag in cacher.cache.entries:
                backup = BackupGenerator.from_cached_version(cacher.cache.entries[tag], tag)
                Path(args.out).write_bytes(backup.backup_data)
            else:
                logger.error("That version isn't installed")
        elif scmd == "restore":
            # Restore a previously-made ZIP backup into the chosen version.
            if tag in cacher.cache.entries:
                BackupRestorer.from_path(Path(args.src)).restore_to_cached_version(
                    cacher.cache.entries[tag]
                )
            else:
                logger.error("That version isn't installed")


if __name__ == "__main__":
    main()
