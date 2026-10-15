#!/usr/bin/env python3
"""Tiny stand-in for the two Tailscale API calls the tailscale role makes, for the unit tests.

  POST   /api/v2/oauth/token     an OAuth client exchanged for an access token:
                                 client_secret tskey-client-good-0000 gets
                                 tskey-api-oauth-0000; any other, 401
  GET    /api/v2/device/<id>     the device: its nodeId and its MagicDNS name,
  DELETE /api/v2/device/<id>     and the device deleted. 200 with a token this
                                 knows for a device it has, 404 for one it has
                                 not (never given, or deleted already), 403 for
                                 tskey-api-denied-0000, 401 for any other
  GET    /fake/deleted           what was deleted, for the tests
  GET    /fake/exchanged         the client ID of every exchange asked for

It never reaches Tailscale: there is no network code but the listener.

Usage: fake_tailscale_api.py <port> [<node id>=<machine name>]...
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

PORT = int(sys.argv[1])
TOKENS = {"tskey-api-good-0000", "tskey-api-oauth-0000"}
FORBIDDEN = "tskey-api-denied-0000"
DEVICES = dict(arg.split("=", 1) for arg in sys.argv[2:])
DELETED: list = []
EXCHANGED: list = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_POST(self):  # http.server API name
        if urlparse(self.path).path != "/api/v2/oauth/token":
            self._send(404, {"message": "not found"})
            return
        form = parse_qs(self._body().decode())
        EXCHANGED.append(form.get("client_id", [""])[0])
        if form.get("client_secret") == ["tskey-client-good-0000"] and form.get("client_id"):
            self._send(200, {"access_token": "tskey-api-oauth-0000", "token_type": "Bearer", "expires_in": 3600})
        else:
            self._send(401, {"message": "invalid client"})

    def _device(self):
        """The device a /api/v2/device/<id> call names, answering for it when it cannot be had; None then."""
        path = urlparse(self.path).path
        device = unquote(path[len("/api/v2/device/") :])
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if token == FORBIDDEN:
            self._send(403, {"message": "access denied"})
        elif token not in TOKENS:
            self._send(401, {"message": "API token invalid"})
        elif device not in DEVICES or device in DELETED:
            self._send(404, {"message": "not found"})
        else:
            return device
        return None

    def do_DELETE(self):  # http.server API name
        if not urlparse(self.path).path.startswith("/api/v2/device/"):
            self._send(404, {"message": "not found"})
            return
        device = self._device()
        if device:
            DELETED.append(device)
            self._send(200, {})

    def do_GET(self):  # http.server API name
        path = urlparse(self.path).path
        if path == "/fake/deleted":
            self._send(200, DELETED)
        elif path == "/fake/exchanged":
            self._send(200, EXCHANGED)
        elif path.startswith("/api/v2/device/"):
            device = self._device()
            if device:
                self._send(200, {"nodeId": device, "name": f"{DEVICES[device]}.tail1234.ts.net", "hostname": "ubuntu"})
        else:
            self._send(404, {"message": "not found"})

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
