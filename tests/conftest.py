"""Shared pytest fixtures."""

import sys
from pathlib import Path

import pytest


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point Path.home() at a scratch directory so tests never touch real state."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture
def no_network(monkeypatch):
    """Make any outbound HTTP call a hard test failure, and record attempts."""
    import requests

    calls: list[str] = []

    def _boom(url, *a, **k):
        calls.append(url)
        raise AssertionError(f"Unexpected network call to {url}")

    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests.Session, "get", lambda self, url, *a, **k: _boom(url))
    return calls


@pytest.fixture
def args():
    """A minimal argparse-like namespace."""
    class Args:
        offline = False
        verbose = False
        launcher = False
        require_digest = False
        use_cached = False
    return Args()


skip_on_windows = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX-specific behaviour"
)
