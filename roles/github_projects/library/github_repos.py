#!/usr/bin/python
# Copyright (c) claude-on-proxmox contributors
# MIT License
"""List the repositories of a GitHub user, following API pagination."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: github_repos
short_description: List repositories of a GitHub user
version_added: "1.0.0"
description:
  - Queries the GitHub REST API for every repository owned by a user and returns
    a compact list suitable for looping over with M(ansible.builtin.git).
  - Handles pagination via the C(Link) header.
  - With a token that belongs to the queried user, private repositories are
    included; otherwise only public ones are visible.
options:
  username:
    description: GitHub login to list repositories for.
    type: str
    required: true
  token:
    description: Optional personal access token.
    type: str
  api_url:
    description: Base URL of the GitHub REST API (override for GitHub Enterprise or tests).
    type: str
    default: https://api.github.com
  include_forks:
    description: Include forked repositories.
    type: bool
    default: false
  include_archived:
    description: Include archived repositories.
    type: bool
    default: false
  per_page:
    description: Page size for API requests (max 100).
    type: int
    default: 100
author:
  - claude-on-proxmox contributors
"""

EXAMPLES = r"""
- name: List public repositories
  github_repos:
    username: octocat
  register: listing

- name: Clone them
  ansible.builtin.git:
    repo: "{{ item.clone_url }}"
    dest: "~/projects/{{ item.name }}"
  loop: "{{ listing.repos }}"
"""

RETURN = r"""
repos:
  description: Repositories after filtering, sorted by name.
  returned: success
  type: list
  elements: dict
  contains:
    name:
      description: Repository name.
      type: str
    full_name:
      description: owner/name.
      type: str
    clone_url:
      description: HTTPS clone URL.
      type: str
    ssh_url:
      description: SSH clone URL.
      type: str
    default_branch:
      description: Default branch name.
      type: str
    fork:
      description: Whether the repository is a fork.
      type: bool
    archived:
      description: Whether the repository is archived.
      type: bool
    private:
      description: Whether the repository is private.
      type: bool
    empty:
      description: True when the repository has never been pushed to (nothing to clone).
      type: bool
total:
  description: Number of repositories returned by the API before filtering.
  returned: success
  type: int
"""

import json
import re
from urllib.parse import quote, urlparse

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url

FIELDS = ("name", "full_name", "clone_url", "ssh_url", "default_branch", "fork", "archived", "private")
_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def parse_next_link(link_header):
    """Return the URL with rel="next" from a Link header, or None."""
    if not link_header:
        return None
    match = _NEXT_LINK.search(link_header)
    return match.group(1) if match else None


def build_headers(token):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ansible-github_repos",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_json(module, url, headers):
    """GET a URL and return (status, parsed_json, info)."""
    response, info = fetch_url(module, url, headers=headers, method="GET", timeout=30)
    status = info.get("status", -1)
    if status != 200:
        body = info.get("body", b"")
        detail = ""
        try:
            detail = json.loads(body).get("message", "")
        except (ValueError, AttributeError, TypeError):
            detail = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
        module.fail_json(msg=f"GitHub API request to {url} failed: HTTP {status} {detail or info.get('msg')}")
    raw = response.read()
    try:
        return json.loads(raw), info
    except ValueError:
        module.fail_json(msg=f"GitHub API request to {url} returned a non-JSON body: {raw[:200]!r}")


def resolve_listing_url(module, api_url, username, token, headers, per_page):
    """Use /user/repos (includes private) when the token belongs to username."""
    if token:
        me, _info = get_json(module, f"{api_url}/user", headers)
        if str(me.get("login", "")).lower() == username.lower():
            return f"{api_url}/user/repos?affiliation=owner&per_page={per_page}"
    return f"{api_url}/users/{quote(username, safe='')}/repos?type=owner&per_page={per_page}"


def fetch_all_pages(module, url, headers):
    repos = []
    while url:
        page, info = get_json(module, url, headers)
        if not isinstance(page, list):
            module.fail_json(msg=f"Unexpected response from {url}: expected a list")
        repos.extend(page)
        url = parse_next_link(info.get("link"))
    return repos


def filter_repos(repos, include_forks, include_archived):
    selected = []
    for repo in repos:
        if repo.get("fork") and not include_forks:
            continue
        if repo.get("archived") and not include_archived:
            continue
        entry = {field: repo.get(field) for field in FIELDS}
        # GitHub leaves pushed_at unset until the first push; such repositories
        # have no branch to check out.
        entry["empty"] = repo.get("pushed_at") is None
        selected.append(entry)
    return sorted(selected, key=lambda r: str(r["name"]).lower())


def check_api_url(module, api_url, token):
    """Refuse to send a token over plain HTTP to anything but loopback (used by tests)."""
    parsed = urlparse(api_url)
    if parsed.scheme not in ("http", "https"):
        module.fail_json(msg=f"api_url must be an http(s) URL, got {api_url!r}")
    if token and parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        module.fail_json(msg=f"Refusing to send a token over plain HTTP to {parsed.hostname}; use https.")


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            username=dict(type="str", required=True),
            token=dict(type="str", no_log=True),
            api_url=dict(type="str", default="https://api.github.com"),
            include_forks=dict(type="bool", default=False),
            include_archived=dict(type="bool", default=False),
            per_page=dict(type="int", default=100),
        ),
        supports_check_mode=True,
    )
    params = module.params
    api_url = params["api_url"].rstrip("/")
    check_api_url(module, api_url, params["token"])
    per_page = max(1, min(100, params["per_page"]))
    headers = build_headers(params["token"])

    url = resolve_listing_url(module, api_url, params["username"], params["token"], headers, per_page)
    all_repos = fetch_all_pages(module, url, headers)
    repos = filter_repos(all_repos, params["include_forks"], params["include_archived"])

    module.exit_json(changed=False, repos=repos, total=len(all_repos))


def main():
    run_module()


if __name__ == "__main__":
    main()
