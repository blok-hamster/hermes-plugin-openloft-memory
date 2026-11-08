"""Test bootstrap for a plugin whose runtime lives inside Hermes.

Two jobs:

1. Expose the plugin as the importable package ``openloft_memory``. The
   on-disk name contains hyphens (fine for Hermes' namespaced loader, invalid
   for importlib), so tests import under a synthetic name served through a
   symlink-mirror directory.

2. Install light-weight ``agent.*`` stand-ins when Hermes is NOT installed.
   Leaves real Hermes modules completely untouched when present.

    What the stubs are:  a plain ``MemoryProvider`` base, a spawn_context_thread
                         that runs a plain daemon thread, and a get_secret that
                         reads one well-known variable.

    What they are NOT:   a snapshot of Hermes behavior. Every test whose value
                         depends on real Hermes semantics (profile-scoped
                         secrets, contextvars-bound threads, the MemoryManager
                         flow) is separately marked to run against a real
                         Hermes installation.

Why a mirror instead of ``__path__ = [plugin_root]``: pytest 9 maps filesystem
directories to imported packages, and a registration claiming the plugin root
directory makes pytest wrap that directory in a Package collector and re-import
the root ``__init__.py`` without a parent package (every test errors at
setup). The mirror keeps ``openloft_memory.*`` imports working while claiming
no directory pytest scans.
"""

import importlib.util
import os
import sys
import tempfile
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = "openloft_memory"

_MIRROR_KEEPALIVE = None


def _register_package() -> None:
    global _MIRROR_KEEPALIVE
    if PKG in sys.modules:
        return
    mirror = tempfile.TemporaryDirectory(prefix="openloft-memory-mirror-")
    base = Path(mirror.name)
    for entry in sorted(ROOT.iterdir()):
        name = entry.name
        if name.startswith(".") or name in {"tests", "scripts"}:
            continue
        if (entry.is_file() and entry.suffix == ".py") or (
                entry.is_dir() and name == "skills"):
            os.symlink(entry.resolve(), base / name)
    spec = importlib.util.spec_from_file_location(
        PKG, str(base / "__init__.py"), submodule_search_locations=[str(base)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = pkg
    spec.loader.exec_module(pkg)  # runs the plugin's real register()
    _MIRROR_KEEPALIVE = mirror    # survives the whole pytest session


def _install_agent_stubs() -> None:
    try:
        if importlib.util.find_spec("agent") is not None:
            return  # real Hermes modules present — never stub over them
    except (ImportError, ValueError):
        pass

    agent = types.ModuleType("agent")

    memory = types.ModuleType("agent.memory_provider")

    class MemoryProvider:  # minimal stand-in, import-name-compatible only
        pass

    def spawn_context_thread(fn, args=(), name="openloft-stub"):
        thread = threading.Thread(target=fn, args=args, daemon=True, name=name)
        thread.start()
        return thread

    memory.MemoryProvider = MemoryProvider
    memory.spawn_context_thread = spawn_context_thread

    secrets = types.ModuleType("agent.secret_scope")

    def get_secret(name: str):
        """Profile-kit stand-in: the one variable the tests control."""
        return (os.environ.get(name, "").strip() or None)

    secrets.get_secret = get_secret

    agent.memory_provider = memory
    agent.secret_scope = secrets
    sys.modules["agent"] = agent
    sys.modules["agent.memory_provider"] = memory
    sys.modules["agent.secret_scope"] = secrets


# Stubs first: executing the package imports `agent.*`, which resolves to the
# Hermes modules when installed and to the stand-ins otherwise.
_install_agent_stubs()
_register_package()
