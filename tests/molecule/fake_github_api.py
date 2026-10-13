#!/usr/bin/env python3
"""Tiny stand-in for the GitHub REST API used by Molecule scenarios and unit tests.

Serves the four requests the github_repos module makes, with a Link header
for pagination exactly like GitHub's, and points clone URLs at local bare
repositories so the github_projects role can be tested without network access:

  /users/<name>          the account (login, id, name)
  /users/<name>/repos    the public repositories, with or without a token
  /user                  the account the token belongs to, 401 without one
  /user/repos            every repository of that account, private included

A token is a stand-in too: "bad-token" is refused, one starting with "stranger"
belongs to another account, and anything else is <name>'s own.

Usage: fake_github_api.py <port> <username> <git_root> <repo-spec>...
  repo-spec: name[:fork][:archived][:empty][:private]
    empty = never pushed, like a fresh GitHub repo; private = listed only to its owner
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(sys.argv[1])
USERNAME = sys.argv[2]
GIT_ROOT = sys.argv[3]
SPECS = sys.argv[4:]
PAGE_SIZE = 2
ACCOUNT = {"login": USERNAME, "id": 4242, "name": "Molecule Tester", "email": None, "type": "User"}
STRANGER = {"login": "stranger", "id": 7, "name": None, "email": None, "type": "User"}


def repos(private_too):
    out = []
    for spec in SPECS:
        name, *flags = spec.split(":")
        if "private" in flags and not private_too:
            continue
        out.append(
            {
                "name": name,
                "full_name": f"{USERNAME}/{name}",
                "clone_url": f"file://{GIT_ROOT}/{name}.git",
                "ssh_url": f"file://{GIT_ROOT}/{name}.git",
                "default_branch": "main",
                "fork": "fork" in flags,
                "archived": "archived" in flags,
                "private": "private" in flags,
                "pushed_at": None if "empty" in flags else "2026-01-01T00:00:00Z",
            }
        )
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _account(self):
        """Who the Authorization header belongs to, or None without a valid token."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer ") :]
        if token == "bad-token":
            return None
        return STRANGER if token.startswith("stranger") else ACCOUNT

    def _page(self, items, url):
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        chunk = items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        headers = []
        if page * PAGE_SIZE < len(items):
            nxt = f"http://{self.headers['Host']}{url.path}?per_page={PAGE_SIZE}&page={page + 1}"
            headers.append(("Link", f'<{nxt}>; rel="next"'))
        self._send(200, chunk, headers)

    def do_GET(self):  # http.server API name
        url = urlparse(self.path)
        if url.path == f"/users/{USERNAME}":
            self._send(200, ACCOUNT)
        elif url.path == f"/users/{USERNAME}/repos":
            self._page(repos(private_too=False), url)
        elif url.path == "/user":
            account = self._account()
            self._send(200, account) if account else self._send(401, {"message": "Bad credentials"})
        elif url.path == "/user/repos":
            account = self._account()
            if account is None:
                self._send(401, {"message": "Requires authentication"})
            else:
                self._page(repos(private_too=account is ACCOUNT), url)
        else:
            self._send(404, {"message": "Not Found"})

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
