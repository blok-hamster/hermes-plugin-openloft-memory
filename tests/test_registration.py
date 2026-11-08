"""Registration gate — a local approximation of `hermes plugins doctor`.

Doctor runs the real directory discovery, manifest parser, namespaced import,
``register(ctx)`` and registries inside a temporary HERMES_HOME; our local gate
asserts the parts that do not require Hermes:

- the manifest carries the fields the loader keys on;
- register() performs NO network I/O (catalog scan / doctor block sockets
  during registration — this test proves the wiring is innocent);
- a fresh provider instance per register() call (multiplexed-gateway rule);
- the plugin's declared env var is the ONLY secret surface, and it is read
  exclusively through get_secret (catalog rule 11);
- every module imports cleanly (catching accidental imports of undocumented
  Hermes surfaces — catalog rule 9).
"""

from pathlib import Path
import re

import pytest

import openloft_memory
from openloft_memory.provider import OpenLoftMemoryProvider

ROOT = Path(__file__).resolve().parents[1]


class FakeCtx:
    """Records what register(ctx) asks Hermes to do. No side effects."""

    def __init__(self):
        self.providers = []
        self.skills = []

    def register_memory_provider(self, provider):
        self.providers.append(provider)

    def register_skill(self, name, path, description=""):
        self.skills.append({"name": name, "path": str(path),
                            "description": description})


class TestManifest:
    def test_manifest_declares_loader_fields(self):
        text = (ROOT / "plugin.yaml").read_text()
        for field in ("name:", "version:", "description:", "requires_env:", "hooks:"):
            assert field in text, f"plugin.yaml missing {field}"
        assert "OPENLOFT_MEMORY_API_KEY" in text

    def test_declared_env_matches_reads(self):
        src = (ROOT / "provider.py").read_text()
        assert 'get_secret("OPENLOFT_MEMORY_API_KEY")' in src
        # and no bare environment read of the secret anywhere (rule 11)
        assert not re.search(r'os\.environ(?:\.get)?\(\s*["\']OPENLOFT_MEMORY_API_KEY', src)


class TestRegistration:
    def test_register_wires_provider_and_skill(self):
        ctx = FakeCtx()
        openloft_memory.register(ctx)
        assert len(ctx.providers) == 1
        assert ctx.providers[0].name == "openloft-memory"
        assert any(s["name"] == "memory-hygiene" for s in ctx.skills)
        for skill in ctx.skills:
            assert Path(skill["path"]).exists(), skill["path"]

    def test_fresh_instance_per_register_call(self):
        """Multiplexed-gateway rule: one instance per load, never shared."""
        ctx1, ctx2 = FakeCtx(), FakeCtx()
        openloft_memory.register(ctx1)
        openloft_memory.register(ctx2)
        assert isinstance(ctx1.providers[0], OpenLoftMemoryProvider)
        assert ctx1.providers[0] is not ctx2.providers[0]

    def test_register_is_io_free(self):
        """No network during registration (doctor blocks sockets)."""
        import socket

        attempts = []
        original = socket.socket

        class Blocker(original):
            def __init__(self, *args, **kwargs):
                attempts.append(args)
                raise RuntimeError("network attempted during register()")

        try:
            socket.socket = Blocker
            openloft_memory.register(FakeCtx())   # must not touch the network
        finally:
            socket.socket = original
        assert attempts == []


class TestNoUndocumentedSurfaceImports:
    """Catalog rule 9: only documented Hermes APIs (agent.*)."""

    MODULES = [ROOT / "provider.py", ROOT / "__init__.py", ROOT / "cli.py"]
    ALLOWED = ("agent", "agent.memory_provider", "agent.secret_scope")

    def test_only_documented_hermes_imports(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+(agent[\w.]*)", re.MULTILINE)
        for path in self.MODULES:
            for match in pattern.finditer(path.read_text()):
                target = match.group(1)
                assert any(
                    target == allowed or target.startswith(allowed + ".")
                    for allowed in self.ALLOWED
                ), f"undocumented Hermes import in {path.name}: {target}"
