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
  - Can also return the account a git identity is derived from (O(account)).
options:
  username:
    description: GitHub login to list repositories for.
    type: str
    required: true
  token:
    description:
      - Optional personal access token. Whitespace around it, such as the
        newline a YAML block scalar leaves, is dropped.
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
  account:
    description:
      - Also return the account a git identity is derived from. With a token,
        that is the token's own account, which the token is checked against
        anyway; without one, it is the user's public profile, one more request
        against an unauthenticated rate limit, so it is made only when asked for.
    type: bool
    default: false
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
user:
  description:
    - The account a git identity is derived from, as GitHub describes it - the
      token's own when a token is given, else the user's when O(account) is
      true - or an empty dict when neither applies.
  returned: success
  type: dict
  contains:
    login:
      description: The login, in GitHub's own spelling.
      type: str
    name:
      description: Display name, or null when the account has none.
      type: str
    id:
      description: Numeric account ID, as GitHub's noreply address for the account uses it.
      type: int
    type:
      description: C(User), or C(Organization) for an account no person commits as.
      type: str
"""

import json
import re
from urllib.parse import quote, urlparse

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url

FIELDS = ("name", "full_name", "clone_url", "ssh_url", "default_branch", "fork", "archived", "private")
USER_FIELDS = ("login", "name", "id", "type")
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


def resolve_listing(module, api_url, username, token, headers, per_page, account):
    """Return the listing URL and the account a git identity is derived from, or {} when none was read.

    /user/repos (private included) when the token is the user's own. A token is
    checked against /user anyway, so its own account - the one that pushes with
    it - comes free. Without a token the account is the user's public profile,
    one more request against the unauthenticated rate limit: made only when
    asked for.
    """
    login = quote(username, safe="")
    public = f"{api_url}/users/{login}/repos?type=owner&per_page={per_page}"
    if token:
        me, _info = get_json(module, f"{api_url}/user", headers)
        if str(me.get("login", "")).lower() == username.lower():
            return f"{api_url}/user/repos?affiliation=owner&per_page={per_page}", me
        return public, me
    if account:
        user, _info = get_json(module, f"{api_url}/users/{login}", headers)
        return public, user
    return public, {}


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
            account=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    params = module.params
    # A trailing newline (a YAML block scalar) makes http.client refuse the
    # header with the token in its message, escaped, so that the no_log
    # masking misses it. Stripped, it is a string the masking was not given:
    # added, so that any message holding it is masked too.
    token = (params["token"] or "").strip()
    if token:
        module.no_log_values.add(token)
    api_url = params["api_url"].rstrip("/")
    check_api_url(module, api_url, token)
    per_page = max(1, min(100, params["per_page"]))
    headers = build_headers(token)

    url, account = resolve_listing(module, api_url, params["username"], token, headers, per_page, params["account"])
    all_repos = fetch_all_pages(module, url, headers)
    repos = filter_repos(all_repos, params["include_forks"], params["include_archived"])
    user = {field: account.get(field) for field in USER_FIELDS} if account else {}

    module.exit_json(changed=False, repos=repos, total=len(all_repos), user=user)


def main():
    run_module()


if __name__ == "__main__":
    main()
