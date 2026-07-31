"""The shared service layer and the GUI's threading policy (R-10)."""

import sys
import threading
import time

import pytest

from gvm import service
from gvm.cache import Cache, CacheEntry, Cacher, Prefs
from gvm.gui_tasks import TaskRunner, parse_event, reap

# --- service: tag resolution ------------------------------------------------

def _cacher(**kw):
    return Cacher(Cache(**kw), None)


def test_resolve_latest_sentinel():
    c = _cacher(default="latest", latest_known="Ghidra_11.4_build")
    assert service.resolve_tag(c, "latest") == "Ghidra_11.4_build"
    assert service.resolve_tag(c, None) == "Ghidra_11.4_build"


def test_resolve_explicit_tag_wins():
    c = _cacher(default="latest", latest_known="Ghidra_11.4_build")
    assert service.resolve_tag(c, "Ghidra_10.0_build") == "Ghidra_10.0_build"


def test_resolve_can_return_empty_before_first_update_check():
    assert service.resolve_tag(_cacher(default="latest", latest_known=""), None) == ""


def test_require_tag_raises_on_the_empty_case():
    """The guard the GUI never had — it used to KeyError instead."""
    c = _cacher(default="latest", latest_known="")
    with pytest.raises(service.ServiceError, match="isn't known yet"):
        service.require_tag(c, None, action="run")


def test_require_tag_passes_through_a_real_tag():
    c = _cacher(default="latest", latest_known="Ghidra_11.4_build")
    assert service.require_tag(c, None) == "Ghidra_11.4_build"


# --- service: runner selection ---------------------------------------------

@pytest.mark.parametrize("pyghidra,expected_fragment", [
    (False, "ghidraRun"),
    (True, "pyghidraRun"),
])
def test_runner_path_honours_pyghidra(tmp_path, pyghidra, expected_fragment):
    c = _cacher(entries={"t": CacheEntry(path=str(tmp_path))})
    runner = service.runner_path(c, "t", pyghidra=pyghidra)
    assert expected_fragment in runner.name or expected_fragment in str(runner)


def test_runner_path_uses_bat_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    c = _cacher(entries={"t": CacheEntry(path=str(tmp_path))})
    assert service.runner_path(c, "t").name.endswith(".bat")


def test_runner_path_unknown_version_raises():
    with pytest.raises(service.ServiceError, match="not installed"):
        service.runner_path(_cacher(), "nope")


def test_check_runner_keeps_record_when_directory_is_missing(tmp_path):
    """An unmounted drive must not silently drop the install record."""
    c = _cacher(entries={"t": CacheEntry(path=str(tmp_path / "gone"))})
    with pytest.raises(service.ServiceError, match="unmounted or moved"):
        service.check_runner(c, "t")
    assert "t" in c.cache.entries


def test_check_runner_drops_record_when_install_is_broken(tmp_path, monkeypatch):
    """Directory present but no runner: genuinely broken, so clear it."""
    monkeypatch.setattr(Cacher, "save", lambda self: None)
    c = _cacher(entries={"t": CacheEntry(path=str(tmp_path))})
    with pytest.raises(service.ServiceError, match="looks broken"):
        service.check_runner(c, "t")
    assert "t" not in c.cache.entries


# --- service: preferences ---------------------------------------------------

def test_set_ui_scale_accepts_valid(tmp_path):
    c = Cacher(Cache(), tmp_path / "cache.toml")
    assert service.set_ui_scale(c, 4) == 4
    assert c.cache.prefs.ui_scale_override == 4


@pytest.mark.parametrize("bad", ["abc", None, 3.5j])
def test_set_ui_scale_rejects_non_integers(tmp_path, bad):
    c = Cacher(Cache(), tmp_path / "cache.toml")
    with pytest.raises(service.ServiceError, match="must be an integer"):
        service.set_ui_scale(c, bad)


@pytest.mark.parametrize("bad", [0, -1, 17, 999])
def test_set_ui_scale_rejects_out_of_range(tmp_path, bad):
    c = Cacher(Cache(), tmp_path / "cache.toml")
    with pytest.raises(service.ServiceError, match="between 1 and 16"):
        service.set_ui_scale(c, bad)


def test_set_install_dir_default_clears_it(tmp_path):
    c = Cacher(Cache(prefs=Prefs(install_dir="/mnt/x")), tmp_path / "cache.toml")
    assert service.set_install_dir(c, "default") == ""
    assert c.cache.prefs.install_dir == ""


def test_set_ext_dir_rejects_a_non_directory(tmp_path):
    c = Cacher(Cache(), tmp_path / "cache.toml")
    missing = tmp_path / "not-there"
    with pytest.raises(service.ServiceError, match="Not a directory"):
        service.set_ext_dir(c, str(missing))


# --- gui_tasks --------------------------------------------------------------

def test_runner_executes_and_reports_done():
    runner = TaskRunner()
    ran = threading.Event()
    assert runner.submit(ran.set) is True
    assert ran.wait(timeout=5)

    for _ in range(100):
        if not runner.busy and any(m is None for m in runner.drain()):
            break
        time.sleep(0.01)
    runner.mark_idle()
    assert runner.busy is False


def test_only_one_task_at_a_time():
    runner = TaskRunner()
    release = threading.Event()
    runner.submit(release.wait)
    assert runner.submit(lambda: None) is False, "a second task was allowed to start"
    release.set()


def test_failing_task_still_posts_the_sentinel():
    """Otherwise the UI would stay 'busy' forever after any error."""
    runner = TaskRunner()

    def boom():
        raise RuntimeError("kaboom")

    runner.submit(boom)
    deadline = time.time() + 5
    messages: list = []
    while time.time() < deadline:
        messages.extend(runner.drain())
        if any(m is None for m in messages):
            break
        time.sleep(0.01)

    assert any(m is None for m in messages), "no completion sentinel after failure"
    assert any(isinstance(m, str) and "kaboom" in m for m in messages)


def test_parse_event_classifies_messages():
    assert parse_event(None) == ("done", None)
    assert parse_event(("__update_available__", "x"))[0] == "event"
    assert parse_event("Loading...") == ("status", "Loading...")


def test_reap_drops_exited_children():
    class Proc:
        def __init__(self, code):
            self._code = code

        def poll(self):
            return self._code

    alive, dead = Proc(None), Proc(0)
    assert reap([alive, dead]) == [alive]
