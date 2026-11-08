"""Shared in-process HTTP stub for client/provider tests. No Hermes needed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class SubstrateStub(BaseHTTPRequestHandler):
    """Contract stub: switches on request content, not on test-side hacks.

    - Authorization containing 'rejected'                → 403
    - agent_id scope_bomb / ratelimit / unprovisioned     → 400 / 429 / 503
    - agent_id boom                                       → 500
    - POST /remember echoes a substrate-shaped response
    - everything else                                     → 200 with an echo
    """

    seen = {}

    def _reply(self, status, payload=None):
        data = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, body=None):
        auth = self.headers.get("Authorization") or ""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "rejected" in auth:
            return self._reply(403, {"detail": "key rejected"})
        key = (body or {}).get("agent_id") or (params.get("agent_id") or [None])[0]
        if key == "scope_bomb":
            return self._reply(400, {"detail": "agent_id may contain only letters, digits, '-' and '_'"})
        if key == "ratelimit":
            return self._reply(429, {"detail": "rate limited"})
        if key == "unprovisioned":
            return self._reply(503, {"detail": "tenant not provisioned: tenant has no key"})
        if key == "boom":
            return self._reply(500, {"detail": "backend exploded"})
        SubstrateStub.seen = {"method": self.command, "path": parsed.path,
                              "query": parsed.query, "auth": auth,
                              "body": body if body else None}
        if parsed.path.endswith("/remember"):
            return self._reply(200, {"tenant": "org_stub", "conversation_id": "default",
                                     "agent_id": "remembered", "turn": 7,
                                     "updates_ingested": 3, "overrides_suppressed": 0,
                                     "consolidated": False})
        self._reply(200, {"ok": True, "echo": body or {"path": self.path},
                          "tenant": "org_stub", "conversation_id": "default",
                          "agent_id": "acme"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self._dispatch(body)

    def do_GET(self):
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:  # keep test output quiet
        pass


def serve_stub():
    """Start the stub on an ephemeral port. Returns (url, httpd)."""
    httpd = HTTPServer(("127.0.0.1", 0), SubstrateStub)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{httpd.server_port}/v1/memory", httpd
