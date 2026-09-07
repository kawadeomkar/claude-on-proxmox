#!/usr/bin/env python3
"""Tiny stand-in for the GitHub REST API used by Molecule scenarios.

Serves paginated /users/<name>/repos responses with a Link header, exactly like
GitHub does, and points clone URLs at local bare repositories so the
github_projects role can be tested without network access.

Usage: fake_github_api.py <port> <username> <git_root> <repo-spec>...
  repo-spec: name[:fork][:archived][:empty]   (empty = never pushed, like a fresh GitHub repo)
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


def repos():
    out = []
    for spec in SPECS:
        name, *flags = spec.split(":")
        out.append(
            {
                "name": name,
                "full_name": f"{USERNAME}/{name}",
                "clone_url": f"file://{GIT_ROOT}/{name}.git",
                "ssh_url": f"file://{GIT_ROOT}/{name}.git",
                "default_branch": "main",
                "fork": "fork" in flags,
                "archived": "archived" in flags,
                "private": False,
                "pushed_at": None if "empty" in flags else "2026-01-01T00:00:00Z",
            }
        )
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # http.server API name
        url = urlparse(self.path)
        if url.path != f"/users/{USERNAME}/repos":
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"message": "Not Found"}')
            return
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        items = repos()
        chunk = items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        body = json.dumps(chunk).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if page * PAGE_SIZE < len(items):
            nxt = f"http://{self.headers['Host']}{url.path}?per_page={PAGE_SIZE}&page={page + 1}"
            self.send_header("Link", f'<{nxt}>; rel="next"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
