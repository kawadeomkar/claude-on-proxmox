#!/usr/bin/env python3
"""Tiny stand-in for the Anthropic Messages API, for running the real claude in a Molecule scenario.

Answers every POST /v1/messages with one short assistant message - streamed as
server-sent events when the request asks for a stream, as JSON otherwise - and
appends each request's JSON body, one per line, to the log file, so a test can
read what claude sent: the system prompt and the history a resume carries.
Anything else is a 404, which claude treats as a feature it can do without.
Never reachable from outside the machine: it listens on 127.0.0.1 only.

Usage: fake_anthropic_api.py <port> <log>
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
LOG = sys.argv[2]
ANSWER = "ok"


def _message(model):
    return {
        "id": "msg_stand_in",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": ANSWER}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _events(model):
    started = {**_message(model), "content": [], "stop_reason": None}
    yield "message_start", {"type": "message_start", "message": started}
    yield (
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )
    yield (
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ANSWER}},
    )
    yield "content_block_stop", {"type": "content_block_stop", "index": 0}
    yield (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
    )
    yield "message_stop", {"type": "message_stop"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self.path.startswith("/v1/messages") or self.path.startswith("/v1/messages/"):
            self.send_error(404)
            return
        try:
            request = json.loads(body)
        except ValueError:
            self.send_error(400)
            return
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps(request) + "\n")
        model = request.get("model", "claude-stand-in")
        if not request.get("stream"):
            data = json.dumps(_message(model)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for event, data in _events(model):
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

    def do_GET(self):
        self.send_error(404)


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
