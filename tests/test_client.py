"""Client contract — dialect, auth, retry policy, error taxonomy.

Runs against the shared in-process stub; no Hermes import needed.
"""

import pytest

from openloft_memory.client import (
    NotProvisioned,
    RateLimited,
    ScopeError,
    ServiceClient,
    ServiceErrorCatchall,
)
from stubserver import serve_stub


@pytest.fixture(scope="module")
def server_url():
    url, httpd = serve_stub()
    yield url
    httpd.shutdown()


def make(server_url, key="nmafc_acme.secret"):
    return ServiceClient(base_url=server_url, api_key=key)


class TestWireFormat:
    def test_bearer_and_snake_case_body(self, server_url):
        make(server_url).recall("hello", agent_id="acme", top_k=8)
        from stubserver import SubstrateStub
        assert SubstrateStub.seen["auth"] == "Bearer nmafc_acme.secret"
        assert SubstrateStub.seen["body"] == {"query": "hello", "top_k": 8, "agent_id": "acme"}

    def test_omitted_conversation_field_stays_omitted(self, server_url):
        # The substrate ignores absent fields; None must never be serialized.
        from stubserver import SubstrateStub
        make(server_url).recall("q", agent_id="acme")
        assert "conversation_id" not in SubstrateStub.seen["body"]

    def test_get_query_string(self, server_url):
        from stubserver import SubstrateStub
        make(server_url).stats(agent_id="acme")
        assert SubstrateStub.seen["path"].endswith("/stats")
        assert SubstrateStub.seen["query"] == "agent_id=acme"

    def test_both_scoped_fields_reachable(self, server_url):
        from stubserver import SubstrateStub
        make(server_url).recall("q", agent_id="acme", conversation_id="conv-1")
        assert SubstrateStub.seen["body"]["conversation_id"] == "conv-1"


class TestTaxonomy:
    def test_403_is_auth_error(self, server_url):
        from openloft_memory.client import AuthError
        with pytest.raises(AuthError) as excinfo:
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

    def test_forget_rejects_hostile_record_id_before_the_wires(self, server_url):
        """The substrate allowlists record ids on its GET paths; the forget
        route does not — so the client refuses harmful values itself rather
        than hoping the server catches them."""
        with pytest.raises(ScopeError):
            make(server_url).forget(agent_id="acme", record_id="x' OR '1'='1")

    def test_forget_accepts_real_record_id_shapes(self, server_url):
        res = make(server_url).forget(agent_id="acme", record_id="4f9c2c1e-1")
        assert res.get("ok") is True


class TestRemember:
    def test_turn_messages_persisted_shape(self, server_url):
        res = make(server_url).remember(
            [{"role": "user", "content": "hi"}], agent_id="remembered")
        assert res["updates_ingested"] == 3
        assert res["turn"] == 7
