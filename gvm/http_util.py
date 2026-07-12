"""Small helpers for talking to GitHub over HTTP."""

import os


def gh_headers(extra: dict | None = None) -> dict:
    """Standard request headers for GitHub API / download calls.

    Always sets a ``User-Agent`` (GitHub requires one). When ``GITHUB_TOKEN`` or
    ``GH_TOKEN`` is set in the environment, adds a bearer ``Authorization``
    header — this lifts the anonymous 60-requests/hour rate limit to 5000/hour,
    which matters for the extension update-check that queries several repos.
    """
    headers = {"User-Agent": "gvm"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers
