"""Run the plugin's substrate contract suite against the REAL memory service.

This boots the production HTTP surface — FastAPI routes, bearer auth + tenant
binding, tenancy cache, LanceDB hot tier, SQLite cold tier — inside this
process, then runs ``pytest tests/test_substrate_contract.py`` in a subprocess.

The ONLY substituted parts are the engine's provider adapters (LLM and
embedding), installed as deterministic fakes at the factory boundary the
engine consults at engine-build time — the same substitution posture the
memory service's own offline tests use. No substrate file is read, written,
or patched on disk. No network egress happens except to this loopback.

Usage:

    # using the service's own test venv (recommended)
    ../backend/memory-service/.venv-test/bin/python scripts/run_contract_suite.py

    # custom service location / port
    MEMORY_SERVICE_DIR=/path/to/memory-service \
    SERVICE_PYTHON=/path/to/venv-test/bin/python \
        python3 scripts/run_contract_suite.py --port 8123 --watch

    --watch   keep serving after the suite exits (curl the service by hand)

Everything written by the run lands in a fresh temp directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from nmafc.integration.base import EmbeddingProvider, LLMProvider

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICE_DIR = PLUGIN_ROOT.parent / "backend" / "memory-service"

# Deterministic minted keys — same secret shapes the service's own tests use.
SECRETS = {"acme": "a" * 40, "other": "b" * 40}


class FakeLLM(LLMProvider):
    """Extraction stand-in: a silent reply and no memory updates. This suite
    is read-only, so remember() is never exercised end-to-end."""

    async def chat_with_extraction(self, messages, system_prompt):
        return "", []

    async def chat(self, messages, system_prompt):
        return ""


class FakeEmbedding(EmbeddingProvider):
    """Deterministic 384-dim vectors (the config's embedding_dim). No model
    download, no ONNX runtime — the ANN query path itself is fully real.
    Per-query embed_single() comes from the engine ABC's default."""

    dim = 384

    async def embed(self, texts):
        out = []
        for text in texts:
            seed = hashlib.blake2b((text or " ").encode("utf-8"), digest_size=16).digest()
            out.append([(seed[i % len(seed)] / 127.5) - 1.0 for i in range(self.dim)])
        return out


def patch_engine_factory() -> None:
    """Replace the provider adapters BEFORE the first engine build.

    The engine's ``NeuromorphicMemory.from_config`` imports the factory
    lazily at call time, so a module-attribute patch here is authoritative.
    """
    import nmafc.integration.factory as factory

    factory.create_llm_provider = lambda *a, **k: FakeLLM()
    factory.create_embedding_provider = lambda *a, **k: FakeEmbedding()


def free_port(preferred: int | None) -> int:
    if preferred:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", preferred))
            return preferred
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Substrate:
    def __init__(self, service_dir: Path, port: int, workdir: Path):
        self.service_dir = service_dir
        self.port = port
        self.workdir = workdir
        self._server = None
        self._thread = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/memory"

    def start(self) -> None:
        sys.path.insert(0, str(self.service_dir))
        os.chdir(self.service_dir)
        os.environ["NMAFC_CONFIG_PATH"] = str(self.service_dir / "config" / "default.toml")

        for name in ("NMAFC_HOT_URI", "NMAFC_COLD_URI", "NMAFC_EVENT_LOG_URI"):
            os.environ[name] = str(self.workdir / name.lower().removeprefix("nmafc_"))
        os.environ["NMAFC_API_KEYS"] = ",".join(
            f"nmafc_{tenant}.{secret}" for tenant, secret in SECRETS.items())
        os.environ["NMAFC_KEYSTORE_PATH"] = str(self.workdir / "issued_keys.json")
        os.environ["NMAFC_EMBEDDING_DIM"] = str(FakeEmbedding.dim)
        os.environ["NMAFC_HOST"] = "127.0.0.1"

        patch_engine_factory()

        from app.main import create_app
        import uvicorn

        config = uvicorn.Config(create_app(), host="127.0.0.1", port=self.port,
                                log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True,
                                        name="substrate-test-server")
        self._thread.start()

    def wait_ready(self, timeout: float = 60.0) -> None:
        root = self.base_url.rsplit("/v1/memory", 1)[0]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{root}/api/health", timeout=2) as resp:
                    body = json.loads(resp.read())
                if body.get("status") in {"ok", "degraded"}:
                    return
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.2)
        raise RuntimeError(f"substrate did not come up on port {self.port}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._thread.join(timeout=15)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=None,
                    help="fixed port (default: a free one)")
    ap.add_argument("--service-dir", default=os.environ.get("MEMORY_SERVICE_DIR")
                    or str(DEFAULT_SERVICE_DIR))
    ap.add_argument("--watch", action="store_true",
                    help="keep serving after the suite exits")
    args = ap.parse_args()

    service_dir = Path(args.service_dir).resolve()
    if not (service_dir / "app" / "main.py").exists():
        sys.exit(f"no memory service at {service_dir} (set --service-dir)")

    with tempfile.TemporaryDirectory(prefix="openloft-substrate-") as tmp:
        workdir = Path(tmp) / "data"
        workdir.mkdir()
        port = free_port(args.port)
        substrate = Substrate(service_dir, port, workdir)
        try:
            substrate.start()
            substrate.wait_ready()
            print(f"substrate booting on {substrate.base_url}  (data: {workdir})")
            for tenant, secret in SECRETS.items():
                print(f"  key[{tenant}] = nmafc_{tenant}.{secret[:6]}…")

            env = dict(os.environ)
            env.update({
                "OPENLOFT_MEMORY_TEST_URL": substrate.base_url,
                "OPENLOFT_MEMORY_TEST_KEYS": ",".join(
                    f"{tenant}=nmafc_{tenant}.{secret}"
                    for tenant, secret in SECRETS.items()),
            })

            suite = PLUGIN_ROOT / "tests" / "test_substrate_contract.py"
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(suite), "-q", "--no-header",
                 "--import-mode=importlib"],
                cwd=str(PLUGIN_ROOT), env=env)
            code = completed.returncode
        finally:
            if args.watch:
                print("watching — Ctrl+C to stop the substrate")
                try:
                    while True:
                        time.sleep(3600)
                except KeyboardInterrupt:
                    pass
            substrate.stop()
    return code if not args.watch else 0


if __name__ == "__main__":
    raise SystemExit(main())
