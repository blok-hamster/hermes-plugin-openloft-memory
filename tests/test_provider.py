"""Provider contract tests — run only inside a Hermes environment.

Outside Hermes the ``agent.*`` modules do not exist, so the whole module
skips (phase-1 tasks 1.8–1.9 run these against a real hermes install).
"""

import pytest

try:
    import agent.memory_provider as _amp  # noqa: F401
    from agent.memory_provider import MemoryManager  # noqa: F401
    HAS_HERMES = True
except Exception:  # noqa: BLE001
    HAS_HERMES = False

pytestmark = pytest.mark.skipif(not HAS_HERMES, reason="Hermes runtime not present")


def _provider():
    from openloft_memory.provider import OpenLoftMemoryProvider
    return OpenLoftMemoryProvider()


def test_is_available_false_without_key(monkeypatch):
    monkeypatch.delenv("OPENLOFT_MEMORY_API_KEY", raising=False)
    assert _provider().is_available() is False


def test_name_is_fixed():
    assert _provider().name == "openloft-memory"


def test_tools_hidden_when_toolset_disabled(monkeypatch, tmp_path):
    prov = _provider()
    import json, pathlib
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "openloft-memory.json").write_text(json.dumps({"toolset_enabled": False}))
    prov._hermes_home = str(home)
    prov._cfg = prov._load_runtime_config()
    assert prov.get_tool_schemas() == []


def test_prefetch_fails_open_when_client_unconfigured(monkeypatch):
    prov = _provider()
    monkeypatch.setattr(prov, "_ready", lambda: False)
    assert prov.prefetch("anything") is None


def test_sync_turn_never_blocks_on_backend_failure():
    """Fail-visible: the turn keeps flowing; the ingest logs the failure.

    Runs inside a Hermes environment only (module-level skip), where
    ``spawn_context_thread`` exists.
    """
    from openloft_memory.client import ServiceClient

    prov = _provider()
    prov._agent_id = "probe"
    prov._cfg = {"ingest_enabled": True, "conversation_scope": "agent"}
    prov._client = ServiceClient(base_url="http://127.0.0.1:1/v1/memory", api_key="k")
    prov.sync_turn("u", "a")            # must raise nothing
    thread = prov._sync_thread
    assert thread is not None
    thread.join(timeout=5.0)            # an unreachable backend curls up, no hang
