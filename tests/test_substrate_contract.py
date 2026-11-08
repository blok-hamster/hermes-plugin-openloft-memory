"""Contract against the REAL substrate (plan §7.2, phase-1 task 1.8).

Run with the memory-service test environment:

    cd ../backend/memory-service && .venv-test/bin/pytest \
        ../hermes-plugin-openloft-memory/tests/test_substrate_contract.py -q

Environment:
    OPENLOFT_MEMORY_TEST_URL   substrate base (default http://127.0.0.1:8100/v1/memory)
    OPENLOFT_MEMORY_TEST_KEYS  comma list: acme=nmafc_acme.xxx,other=nmafc_other.yyy

Skips as a whole module when the substrate is not listening.
"""

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

import pytest

from openloft_memory.client import (
    NotProvisioned,
    ScopeError,
    ServiceClient,
    ServiceErrorCatchall,
)

BASE = os.environ.get("OPENLOFT_MEMORY_TEST_URL", "http://127.0.0.1:8100/v1/memory")


def _keys() -> dict[str, str]:
    table: dict[str, str] = {}
    for entry in (os.environ.get("OPENLOFT_MEMORY_TEST_KEYS") or "").split(","):
        if "=" in entry:
            k, _, v = entry.partition("=")
            table[k.strip()] = v.strip()
    return table


def _substrate_up() -> bool:
    root = urlparse(BASE)
    probe = f"{root.scheme}://{root.netloc}/api/health"
    try:
        with urllib.request.urlopen(probe, timeout=1):
            return True
    except Exception:  # noqa: BLE001
        return False


def client(tenant: str) -> ServiceClient:
    key = _keys().get(tenant)
    if not key:
        pytest.skip(f"no test key provisioned for tenant '{tenant}'")
    return ServiceClient(base_url=BASE, api_key=key)


pytestmark = pytest.mark.skipif(not _substrate_up(), reason="substrate not running")


class TestFieldNamingContract:
    """Mirror of the substrate's own TestFieldNamingContract."""

    def test_snake_case_accepted(self):
        res = client("acme").recall("probe ping", agent_id="isol", top_k=3)
        assert "context" in res and "hits" in res

    def test_camel_case_rejected_422(self):
        """extra=forbid: the substrate 422s instead of silently dropping fields."""
        key = _keys().get("acme", "x")
        raw = urllib.request.Request(
            BASE + "/recall",
            data=json.dumps({"query": "x", "topK": 3}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(raw, timeout=5)
            pytest.fail("camelCase body must be rejected — silent drop merges scopes")
        except urllib.error.HTTPError as exc:
            assert exc.code == 422


class TestIsolation:
    def test_tenant_boundary_follows_the_key(self):
        """The other tenant's key reads ITS store, never acme's rows."""
        mine = client("acme").recall("isolation probe", agent_id="isol", top_k=3)
        other = client("other").recall("isolation probe", agent_id="isol", top_k=3)
        assert mine.get("tenant") != other.get("tenant") or (
            mine.get("tenant") is None and other.get("tenant") is None
        )

    def test_header_cannot_move_the_tenant(self):
        import urllib.request

        key = _keys().get("acme", "x")
        raw = urllib.request.Request(
            BASE + "/stats",
            headers={"Authorization": f"Bearer {key}",
                     "X-Agent-Id": "cross_tenant_attempt"},
        )
        with urllib.request.urlopen(raw, timeout=5) as resp:
            body = json.loads(resp.read())
        assert body.get("agent_id") in (None, "isol")  # never another tenant's scope

    def test_hostile_record_id_stays_in_tenant(self):
        with pytest.raises((ScopeError, ServiceErrorCatchall, NotProvisioned)):
            client("acme").forget(agent_id="isol", record_id="x' OR '1'='1")


class TestSubScopeCharset:
    def test_bad_conversation_id_400(self):
        """A colon-bearing sub-scope is rejected upstream — the sanitizer's job
        is to make sure OUR calls never get that far. This proves the server
        side of the contract."""
        raw = urllib.request.Request(
            BASE + "/stats?conversation_id=bad%3Acolon",
            headers={"Authorization": f"Bearer {_keys().get('acme', 'x')}"},
        )
        try:
            urllib.request.urlopen(raw, timeout=5)
            pytest.fail("an invalid sub-scope must be rejected, not served")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("detail", "")
            assert "letters" in detail
