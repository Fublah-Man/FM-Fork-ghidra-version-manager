"""Shared HTTP helpers for GVM's GitHub calls.

Two jobs:

* Build consistent request headers, honouring ``GITHUB_TOKEN`` /
  ``GH_TOKEN`` when the user has one set. Anonymous GitHub API access is capped
  at 60 requests/hour, which a user managing several versions and extensions can
  hit easily; an authenticated token raises that to 5,000.
* Provide a single ``requests.Session`` so connections are reused across the
  several calls a single command makes, instead of opening a new TLS connection
  each time.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "gvm"

_session: requests.Session | None = None


def github_token() -> str | None:
    """Return a GitHub token from the environment, if the user set one."""
    for var in ("GVM_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    return None


def github_headers() -> dict[str, str]:
    """Standard headers for a GitHub API request."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    token = github_token()
    if token:
        # Never log the token itself.
        headers["Authorization"] = f"Bearer {token}"
    return headers


def session() -> requests.Session:
    """Return the process-wide session, creating it on first use."""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT})
    return _session


def get(url: str, **kwargs) -> requests.Response:
    """``requests.get`` with GVM's standard headers and connection reuse."""
    headers = github_headers()
    headers.update(kwargs.pop("headers", None) or {})
    kwargs.setdefault("timeout", 30)
    return session().get(url, headers=headers, **kwargs)
