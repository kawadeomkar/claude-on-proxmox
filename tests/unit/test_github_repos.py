"""Unit tests for roles/github_projects/library/github_repos.py."""

from __future__ import annotations

import io
import json

import pytest
from conftest import fail_main, load_module, run_main

github_repos = load_module("roles/github_projects/library/github_repos.py")

API = "https://api.github.com"


def repo(name, **overrides):
    data = {
        "name": name,
        "full_name": f"octo/{name}",
        "clone_url": f"https://github.com/octo/{name}.git",
        "ssh_url": f"git@github.com:octo/{name}.git",
        "default_branch": "main",
        "fork": False,
        "archived": False,
        "private": False,
        "pushed_at": "2026-01-01T00:00:00Z",
        "html_url": f"https://github.com/octo/{name}",  # extra field must be dropped
    }
    data.update(overrides)
    return data


class FakeAPI:
    """Minimal stand-in for fetch_url keyed by exact URL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, module, url, headers=None, method="GET", timeout=None):
        self.calls.append((url, headers))
        if url not in self.routes:
            return None, {"status": 404, "body": b'{"message": "Not Found"}', "url": url}
        status, body, extra = self.routes[url]
        info = {"status": status, "url": url}
        info.update(extra)
        if status == 200:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            return io.BytesIO(payload), info
        if status == -1:  # transport failure: no body at all
            return None, info
        return None, dict(info, body=json.dumps(body).encode())


@pytest.fixture
def fake_api(monkeypatch):
    def _install(routes):
        api = FakeAPI(routes)
        monkeypatch.setattr(github_repos, "fetch_url", api)
        return api

    return _install


def run(args):
    return run_main(github_repos.main, args)


def fail(args):
    return fail_main(github_repos.main, args)


# --------------------------------------------------------------- helpers ----


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ('<https://x/repos?page=2>; rel="next", <https://x/repos?page=5>; rel="last"', "https://x/repos?page=2"),
        ('<https://x/repos?page=1>; rel="prev", <https://x/repos?page=1>; rel="first"', None),
        ('<https://x/a?page=3>;rel="next"', "https://x/a?page=3"),
    ],
)
def test_parse_next_link(header, expected):
    assert github_repos.parse_next_link(header) == expected


def test_filter_repos_drops_forks_and_archived_by_default():
    repos = [repo("b"), repo("a", fork=True), repo("c", archived=True)]
    assert [r["name"] for r in github_repos.filter_repos(repos, False, False)] == ["b"]
    assert [r["name"] for r in github_repos.filter_repos(repos, True, False)] == ["a", "b"]
    assert [r["name"] for r in github_repos.filter_repos(repos, False, True)] == ["b", "c"]


def test_filter_repos_returns_only_known_fields_sorted_case_insensitively():
    result = github_repos.filter_repos([repo("Zeta"), repo("alpha")], False, False)
    assert [r["name"] for r in result] == ["alpha", "Zeta"]
    assert set(result[0]) == set(github_repos.FIELDS) | {"empty"}


def test_filter_repos_flags_never_pushed_repositories_as_empty():
    result = github_repos.filter_repos([repo("fresh", pushed_at=None), repo("used")], False, False)
    assert [(r["name"], r["empty"]) for r in result] == [("fresh", True), ("used", False)]


def test_build_headers_with_and_without_token():
    assert "Authorization" not in github_repos.build_headers(None)
    assert github_repos.build_headers("tok")["Authorization"] == "Bearer tok"


# ---------------------------------------------------------------- module ----


def test_lists_public_repos_with_pagination(fake_api, patch_module):
    page1 = f"{API}/users/octo/repos?type=owner&per_page=2"
    page2 = f"{API}/users/octo/repos?type=owner&per_page=2&page=2"
    api = fake_api(
        {
            page1: (200, [repo("one"), repo("two", fork=True)], {"link": f'<{page2}>; rel="next"'}),
            page2: (200, [repo("three")], {}),
        }
    )

    result = run({"username": "octo", "per_page": 2})

    assert result["changed"] is False
    assert result["total"] == 3
    assert [r["name"] for r in result["repos"]] == ["one", "three"]
    assert [c[0] for c in api.calls] == [page1, page2]
    assert "Authorization" not in api.calls[0][1]


def test_uses_authenticated_endpoint_when_token_matches_user(fake_api, patch_module):
    api = fake_api(
        {
            f"{API}/user": (200, {"login": "Octo"}, {}),
            f"{API}/user/repos?affiliation=owner&per_page=100": (200, [repo("secret", private=True)], {}),
        }
    )

    result = run({"username": "octo", "token": "tok"})

    assert [r["name"] for r in result["repos"]] == ["secret"]
    assert result["repos"][0]["private"] is True
    assert api.calls[0][1]["Authorization"] == "Bearer tok"


def test_token_for_other_user_falls_back_to_public_listing(fake_api, patch_module):
    api = fake_api(
        {
            f"{API}/user": (200, {"login": "someone-else"}, {}),
            f"{API}/users/octo/repos?type=owner&per_page=100": (200, [repo("pub")], {}),
        }
    )

    result = run({"username": "octo", "token": "tok"})

    assert [r["name"] for r in result["repos"]] == ["pub"]
    assert api.calls[-1][0].startswith(f"{API}/users/octo/repos")


def test_custom_api_url_and_trailing_slash(fake_api, patch_module):
    fake_api({"http://127.0.0.1:8000/users/octo/repos?type=owner&per_page=100": (200, [repo("x")], {})})

    result = run({"username": "octo", "api_url": "http://127.0.0.1:8000/"})

    assert result["total"] == 1


def test_api_error_is_reported(fake_api, patch_module):
    fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (403, {"message": "rate limit exceeded"}, {})})
    result = fail({"username": "octo"})

    assert "HTTP 403" in result["msg"]
    assert "rate limit exceeded" in result["msg"]


def test_unknown_user_is_reported(fake_api, patch_module):
    fake_api({})
    result = fail({"username": "nobody"})

    assert "HTTP 404" in result["msg"]


def test_non_list_response_fails(fake_api, patch_module):
    fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (200, {"oops": True}, {})})
    result = fail({"username": "octo"})

    assert "expected a list" in result["msg"]


def test_per_page_is_clamped(fake_api, patch_module):
    api = fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (200, [], {})})

    result = run({"username": "octo", "per_page": 500})

    assert result["repos"] == []
    assert api.calls[0][0].endswith("per_page=100")


def test_include_archived_through_module_entry_point(fake_api, patch_module):
    fake_api(
        {
            f"{API}/users/octo/repos?type=owner&per_page=100": (
                200,
                [repo("old", archived=True), repo("live")],
                {},
            )
        }
    )

    result = run({"username": "octo", "include_archived": True})

    assert [r["name"] for r in result["repos"]] == ["live", "old"]


def test_username_is_url_escaped(fake_api, patch_module):
    api = fake_api({f"{API}/users/we%2Fird%3Fx/repos?type=owner&per_page=100": (200, [], {})})

    run({"username": "we/ird?x"})

    assert api.calls[0][0] == f"{API}/users/we%2Fird%3Fx/repos?type=owner&per_page=100"


def test_transport_failure_reports_module_utils_message(fake_api, patch_module):
    fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (-1, None, {"msg": "connection refused"})})

    result = fail({"username": "octo"})

    assert "HTTP -1" in result["msg"]
    assert "connection refused" in result["msg"]


def test_non_json_success_body_is_reported(fake_api, patch_module):
    fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (200, b"<html>captive portal</html>", {})})

    result = fail({"username": "octo"})

    assert "non-JSON body" in result["msg"]


def test_non_json_error_body_falls_back_to_raw_text(fake_api, patch_module):
    fake_api({f"{API}/users/octo/repos?type=owner&per_page=100": (502, "Bad Gateway", {})})

    result = fail({"username": "octo"})

    assert "HTTP 502" in result["msg"]
    assert "Bad Gateway" in result["msg"]


def test_bad_token_is_reported_from_user_lookup(fake_api, patch_module):
    fake_api({f"{API}/user": (401, {"message": "Bad credentials"}, {})})

    result = fail({"username": "octo", "token": "bad"})

    assert "HTTP 401" in result["msg"]
    assert "Bad credentials" in result["msg"]
    assert "bad" not in result["msg"].split("Bad credentials")[0]


def test_per_page_lower_clamp(fake_api, patch_module):
    api = fake_api({f"{API}/users/octo/repos?type=owner&per_page=1": (200, [], {})})

    run({"username": "octo", "per_page": 0})

    assert api.calls[0][0].endswith("per_page=1")


def test_token_over_plain_http_is_refused_except_loopback(fake_api, patch_module):
    result = fail({"username": "octo", "token": "tok", "api_url": "http://ghe.example.com"})
    assert "plain HTTP" in result["msg"]

    fake_api(
        {
            "http://127.0.0.1:8000/user": (200, {"login": "other"}, {}),
            "http://127.0.0.1:8000/users/octo/repos?type=owner&per_page=100": (200, [], {}),
        }
    )
    assert run({"username": "octo", "token": "tok", "api_url": "http://127.0.0.1:8000"})["total"] == 0


def test_invalid_scheme_is_refused(patch_module):
    result = fail({"username": "octo", "api_url": "ftp://api.github.com"})
    assert "http(s)" in result["msg"]
