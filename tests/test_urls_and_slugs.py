"""URL parsing and slug generation (M-1, M-2, L-1)."""

import pytest

from gvm.extensions import _GH_NAME_RE, _generate_slug, parse_git_url


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/owner/repo", ("owner", "repo")),
    ("https://github.com/owner/repo.git", ("owner", "repo")),
    ("http://github.com/owner/repo/", ("owner", "repo")),
    ("git@github.com:owner/repo.git", ("owner", "repo")),
    ("https://github.com/owner/repo/tree/main", ("owner", "repo")),
    ("owner/repo", ("owner", "repo")),
])
def test_parses_github_urls(url, expected):
    assert parse_git_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://evil.com/owner/repo",
    "https://gitlab.com/owner/repo",
    "git@evil.com:owner/repo.git",
])
def test_rejects_non_github_hosts(url):
    """M-2: the host used to be discarded, silently substituting github.com."""
    with pytest.raises(ValueError, match="Only github.com"):
        parse_git_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/../../etc/repo",
    "https://github.com/./repo",
    "https://github.com/owner",
    "https://github.com/",
    "not a url at all",
])
def test_rejects_malformed_or_traversing_paths(url):
    with pytest.raises(ValueError):
        parse_git_url(url)


@pytest.mark.parametrize("name", ["..", ".", "...", "-lead", "_lead"])
def test_gh_name_regex_rejects_dot_names(name):
    """M-1: the old pattern matched '..' despite claiming otherwise."""
    assert not _GH_NAME_RE.match(name)


@pytest.mark.parametrize("name", ["owner", "my-repo", "a.b", "x_y", "a1"])
def test_gh_name_regex_accepts_real_names(name):
    assert _GH_NAME_RE.match(name)


def test_degenerate_names_get_distinct_slugs():
    """L-1: these all collapsed to 'local-' and overwrote each other."""
    a = _generate_slug("!!!", "directory")
    b = _generate_slug("   ", "directory")
    c = _generate_slug("???", "directory")
    assert a != b != c and a != c
    assert all(s.startswith("local-") and len(s) > len("local-") for s in (a, b, c))


def test_normal_names_keep_readable_slugs():
    assert _generate_slug("FindCrypt", "directory") == "local-findcrypt"
    assert _generate_slug("My Extension", "directory") == "local-my-extension"


def test_slug_is_deterministic():
    assert _generate_slug("!!!", "directory") == _generate_slug("!!!", "directory")


def test_slug_length_is_bounded():
    assert len(_generate_slug("a" * 500, "directory")) <= 90
