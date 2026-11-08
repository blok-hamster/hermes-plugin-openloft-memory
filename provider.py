"""OpenLoftMemoryProvider — Hermes MemoryProvider ABC implementation.

Implements the lifecycle mapping pinned in ../HERMES_MEMORY_PLUGIN_PLAN.md §3:

- prefetch          → POST /recall, fail-open, budgeted, injected per turn
- queue_prefetch    → background warm-up only, never blocks
- sync_turn         → background ingest via ``spawn_context_thread``
  (catalog-required: contextvars/profile isolation; join-first, one in flight)
- tools             → loft_recall / loft_remember / loft_forget / loft_stats /
                      loft_search; JSON-always handlers via schemas + client
- config            → profile-aware secret via get_secret + flat runtime JSON

Everything profile-specific is bound on the instance in initialize(); nothing
is stored at module or class level (multiplexed-gateway rule).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

# Documented Hermes surfaces only (catalog rule 9).
from agent.memory_provider import MemoryProvider, spawn_context_thread
from agent.secret_scope import get_secret

from .client import (
    NotProvisioned,
    RateLimited,
    ScopeError,
    ServiceClient,
)
from .config_schema import (
    DEFAULT_BASE_URL,
    FIELDS,
    runtime_config,
    runtime_config_path,
)
from .schemas import (
    SYSTEM_PROMPT_BLOCK,
    friendly_error,
    shrink,
    tool_schemas,
    turn_payload,
)
from .scope import resolve_agent_id, resolve_conversation_id

logger = logging.getLogger("hermes.plugins.openloft-memory")

PLUGIN_NAME = "openloft-memory"

_SYNC_JOIN_TIMEOUT = 5.0   # how long a turn's ingest waits for its predecessor
# The recall wall budget is enforced by the client's per-socket timeout
# (DEFAULT_TIMEOUTS["recall"]); this constant is documentation, not enforcement.
_PREFETCH_BUDGET_SECONDS = 8.0


def _env(name: str) -> str | None:
    """Non-secret env knobs only. The key never comes from here (rule 11)."""
    value = os.environ.get(name)
    return value if value else None


class OpenLoftMemoryProvider(MemoryProvider):
    """Fail-open reads, fail-visible writes, profile-isolated by construction."""

    def __init__(self) -> None:
        self._client: ServiceClient | None = None
        self._agent_id = "hermes"
        self._conv_mode = "agent"
        self._cfg: dict = {}
        self._identity = ""
        self._context_kind = "primary"
        self._sync_thread: threading.Thread | None = None

    # -- required ABC surface -------------------------------------------------

    @property
    def name(self) -> str:
        return PLUGIN_NAME

    def is_available(self) -> bool:
        """Credential presence only — network-free by contract."""
        try:
            return bool(get_secret("OPENLOFT_MEMORY_API_KEY"))
        except Exception:  # noqa: BLE001 - a secret-scope miss must not break agent init
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Bind-only: no network, no writes. All storage is remote."""
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._platform = str(kwargs.get("platform") or "cli")
        self._identity = str(kwargs.get("agent_identity") or "")
        self._context_kind = str(kwargs.get("agent_context") or "primary")

        cfg = self._load_runtime_config()
        self._cfg = cfg
        self._agent_id = resolve_agent_id(
            cfg_override=cfg.get("agent_id"),
            env_id=_env("OPENLOFT_MEMORY_AGENT_ID"),
            profile_identity=self._identity,
        )
        self._conv_mode = cfg.get("conversation_scope") or "agent"

        base = _env("OPENLOFT_MEMORY_BASE_URL") or str(cfg.get("base_url") or DEFAULT_BASE_URL)
        key = ""
        try:
            key = get_secret("OPENLOFT_MEMORY_API_KEY") or ""
        except Exception:  # noqa: BLE001
            key = ""
        self._client = ServiceClient(base_url=base, api_key=key)

    def get_config_schema(self) -> list:
        return [dict(f) for f in FIELDS]

    def get_tool_schemas(self) -> list:
        if not self._cfg.get("toolset_enabled", True):
            return []
        scope = self._conversation_scope()
        suffix = "" if scope is None else f" (this agent: {scope})"
        return tool_schemas(suffix)

    # -- lifecycle hooks --------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "", **kwargs):
        """Synchronous, budgeted, fail-open recall. Returns injectable context."""
        if not self._ready():
            return None
        try:
            res = self._client.recall(
                query[:4000],
                agent_id=self._agent_id,
                top_k=int(self._cfg.get("top_k", 8)),
                conversation_id=self._conversation_scope(session_id),
            )
        except NotProvisioned as exc:
            logger.warning("openloft-memory: not provisioned — %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - fail-open reads
            logger.warning("openloft-memory: prefetch degraded (%s)", exc)
            return None

        context = res.get("context") or ""
        if not context.strip():
            return None
        return {"context": shrink(context, int(self._cfg.get("inject_budget", 6000)))}

    def queue_prefetch(self, query: str, *, session_id: str = "", **kwargs) -> None:
        """Fire-and-forget warm-up; result discarded."""
        if not self._ready() or not self._cfg.get("warm_next_turn", True):
            return
        def job() -> None:
            self._warm(query, session_id)
        try:
            self._spawn(job, "openloft-memory-warm")
        except Exception as exc:  # noqa: BLE001
            logger.debug("openloft-memory: warm skipped (%s)", exc)

    def sync_turn(self, user, assistant, *, session_id: str = "", messages=None, **kwargs) -> None:
        """Non-blocking ingest: join-first, one in flight, never blocks a turn."""
        if not self._ready() or not self._cfg.get("ingest_enabled", True):
            return
        if self._context_kind not in {"primary", ""} and not self._cfg.get("ingest_nonprimary"):
            return
        payload = list(messages) if messages else turn_payload(user, assistant)
        if not payload:
            return
        previous = self._sync_thread
        def job() -> None:
            self._ingest(payload, session_id, previous)
        self._sync_thread = self._spawn(job, "openloft-memory-sync")

    def system_prompt_block(self) -> str | None:
        return SYSTEM_PROMPT_BLOCK

    def on_session_end(self, messages, **kwargs) -> None:
        """Turn-level ingest already persisted; nothing to flush in v1."""

    def shutdown(self) -> None:
        thread = self._sync_thread
        if thread is not None:
            try:
                thread.join(timeout=_SYNC_JOIN_TIMEOUT)
            except RuntimeError:  # join from the same thread
                pass

    # -- config write path -------------------------------------------------------

    def save_config(self, values: dict, hermes_home: str) -> None:
        """Persist non-secret settings (plan §4.1).

        Path convention from the Desktop panel (STORAGE_FLAT_JSON):
        ``$HERMES_HOME/<name>/config.json`` — the same file the panel edits,
        so a dashboard change and our runtime binding never diverge.
        """
        target = runtime_config_path(hermes_home)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, target)
        # Rebind so a runtime change takes effect without a restart.
        self._cfg = runtime_config(values, hermes_home)
        if self._client is not None:
            self._client.base_url = (
                _env("OPENLOFT_MEMORY_BASE_URL")
                or str(self._cfg.get("base_url") or DEFAULT_BASE_URL)
            ).rstrip("/")

    # -- tool calls ----------------------------------------------------------------

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        try:
            return json.dumps(self._dispatch(tool_name, dict(args or {})))
        except Exception as exc:  # noqa: BLE001 - handlers never raise
            return json.dumps({"error": friendly_error(exc)})

    def _dispatch(self, tool_name: str, args: dict) -> dict:
        if not self._ready():
            raise NotProvisioned("memory backend is not configured or keyless")
        conversation_id = self._conversation_scope(str(args.get("_session_id") or ""))
        if tool_name == "loft_recall":
            return self._client.recall(
                str(args.get("query") or ""),
                agent_id=self._agent_id,
                top_k=int(args.get("top_k") or self._cfg.get("top_k", 8)),
                conversation_id=conversation_id,
            )
        if tool_name == "loft_remember":
            text = str(args.get("text") or "").strip()
            if not text:
                raise ScopeError("loft_remember needs text to remember")
            return self._client.remember(
                [{"role": "user", "content": text[:20000]}],
                agent_id=self._agent_id,
                conversation_id=conversation_id,
            )
        if tool_name == "loft_forget":
            return self._client.forget(
                agent_id=self._agent_id,
                entity_name=args.get("entity_name"),
                record_id=args.get("record_id"),
                conversation_id=conversation_id,
            )
        if tool_name == "loft_stats":
            return self._client.stats(agent_id=self._agent_id, conversation_id=conversation_id)
        if tool_name == "loft_search":
            return self._client.search(
                str(args.get("query") or ""),
                agent_id=self._agent_id,
                limit=int(args.get("limit") or 10),
                conversation_id=conversation_id,
            )
        raise ScopeError(f"unknown tool '{tool_name}' — valid: loft_recall, loft_remember, "
                         "loft_forget, loft_stats, loft_search")

    # -- internals -------------------------------------------------------------------

    def _spawn(self, job, name: str):
        """spawn_context_thread with both documented start semantics tolerated.

        The threading contract gives ``spawn_context_thread(fn, name=...)`` a
        contextvars-wired thread; whether that helper starts the thread itself
        or hands it back for an explicit .start() is not observable without a
        Hermes install, so we handle both and neither ever runs un-started.
        """
        try:
            thread = spawn_context_thread(job, name=name)
        except TypeError:
            thread = spawn_context_thread(job)
        try:
            if not thread.is_alive():
                thread.start()
        except (RuntimeError, AttributeError):
            pass  # already started, or the helper returned None-style behavior
        return thread

    def _ready(self) -> bool:
        return bool(self._client and self._client.api_key)

    def _conversation_scope(self, session_id: str = "") -> str | None:
        return resolve_conversation_id(self._conv_mode, session_id)

    def _load_runtime_config(self) -> dict:
        try:
            raw = runtime_config_path(self._hermes_home).read_text() if self._hermes_home else ""
        except OSError:
            raw = ""
        try:
            saved = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            logger.warning("openloft-memory: config.json did not parse; using defaults")
            saved = {}
        return runtime_config(saved, self._hermes_home)

    def _warm(self, query: str, session_id: str) -> None:
        try:
            self._client.recall(
                query[:4000],
                agent_id=self._agent_id,
                top_k=int(self._cfg.get("top_k", 8)),
                conversation_id=self._conversation_scope(session_id),
            )
        except Exception:  # noqa: BLE001 - warm-up is best-effort
            pass

    def _ingest(self, payload: list, session_id: str, previous: threading.Thread | None) -> None:
        if previous is not None:
            try:
                previous.join(timeout=_SYNC_JOIN_TIMEOUT)
            except RuntimeError:
                pass
        try:
            res = self._client.remember(
                payload,
                agent_id=self._agent_id,
                conversation_id=self._conversation_scope(session_id),
            )
            logger.info(
                "openloft-memory: ingested %s updates (turn %s)",
                res.get("updates_ingested"), res.get("turn"),
            )
        except RateLimited as exc:
            logger.warning("openloft-memory: ingest rate-limited (next turn re-sends a "
                           "fuller transcript); %s", exc)
        except Exception as exc:  # noqa: BLE001 - never fail the turn for memory
            logger.error("openloft-memory: ingest failed (%s); the next turn's "
                         "fuller transcript is the recovery path", exc)
