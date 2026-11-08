"""Client contract — dialect, auth, retry policy, error taxonomy.

Runs against a local stub HTTP server; no Hermes import needed.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from openloft_memory.client import (
    NotProvisioned,
    RateLimited,
    ScopeError,
    ServiceClient,
    ServiceErrorCatchall,
)


class Stub(BaseHTTPRequestHandler):
    """Contract stub: switches by request content, not by test-side hacks.

    - Authorization containing 'rejected'            → 403
    - agent_id scope_bomb / ratelimit / unprovisioned → 400 / 429 / 503
    - agent_id boom                                  → 500
    - everything else                                → 200 with an echo
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
        Stub.seen = {"method": self.command, "path": parsed.path, "query": parsed.query,
                     "auth": auth, "body": body or None}
        self._reply(200, {"ok": True, "echo": body or {"path": self.path}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        self._dispatch(body)

    def do_GET(self):
        self._dispatch()

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture(scope="module")
def server_url():
    httpd = HTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1/memory"
    httpd.shutdown()
    thread.join(timeout=2)


def make(server_url, key="nmafc_acme.secret"):
    return ServiceClient(base_url=server_url, api_key=key)


class TestWireFormat:
    def test_bearer_and_snake_case_body(self, server_url):
        make(server_url).recall("hello", agent_id="acme", top_k=8)
        assert Stub.seen["auth"] == "Bearer nmafc_acme.secret"
        assert Stub.seen["body"] == {"query": "hello", "top_k": 8, "agent_id": "acme"}

    def test_omitted_conversation_field_stays_omitted(self, server_url):
        # The substrate ignores absent fields; None must never be serialized.
        make(server_url).recall("q", agent_id="acme")
        assert "conversation_id" not in Stub.seen["body"]

    def test_get_query_string(self, server_url):
        make(server_url).stats(agent_id="acme")
        assert Stub.seen["path"].endswith("/stats")
        assert Stub.seen["query"] == "agent_id=acme"

    def test_both_scoped_fields_reachable(self, server_url):
        make(server_url).recall("q", agent_id="acme", conversation_id="conv-1")
        assert Stub.seen["body"]["conversation_id"] == "conv-1"


class TestTaxonomy:
    def test_403_is_auth_error(self, server_url):
        with pytest.raises(Exception) as excinfo:
            make(server_url, key="rejected-key").forget(agent_id="acme", entity_name="x")
        assert excinfo.value.status == 403

    def test_400_is_scope_error(self, server_url):
        with pytest.raises(ScopeError) as excinfo:
            make(server_url).stats(agent_id="scope_bomb")
        assert "letters" in str(excinfo.value)

    def test_503_not_provisioned(self, server_url):
        with pytest.raises(NotProvisioned) as excinfo:
            make(server_url).stats(agent_id="unprovisioned")
        assert excinfo.value.status == 503

    def test_429_is_rate_limited(self, server_url):
        with pytest.raises(RateLimited):
            make(server_url).stats(agent_id="ratelimit")

    def test_500_is_catchall(self, server_url):
        with pytest.raises(ServiceErrorCatchall):
            make(server_url).search("q", agent_id="boom")


class TestForgetShape:
    def test_forget_needs_exactly_one_target(self, server_url):
        with pytest.raises(ScopeError):
            make(server_url).forget(agent_id="acme")
        with pytest.raises(ScopeError):
            make(server_url).forget(agent_id="acme", entity_name="e", record_id="r")
