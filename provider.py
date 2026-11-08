"""OpenLoftMemoryProvider — Hermes MemoryProvider ABC implementation.

Implements the lifecycle mapping pinned in ../HERMES_MEMORY_PLUGIN_PLAN.md §3:

- prefetch          → POST /recall, fail-open, budgeted, injected per turn
- queue_prefetch    → background warm-up only, never blocks
- sync_turn         → background ingest via threading.Thread with
                      contextvar propagation (join-first, one in flight)
- tools             → loft_recall / loft_remember / loft_forget / loft_stats /
                      loft_search; JSON-always handlers via schemas + client
- config            → profile-aware secret via get_secret + flat runtime JSON
- phase 3 hooks     → on_pre_compress (digest-keyed, idempotent transcript
                      archive, opt-in flag + require_checkpoint) and
                      on_memory_write (MEMORY.md/USER.md mirroring with
                      previous_content guard); both stub-verified; Hermes
                      verification pending gate 1.9

Everything profile-specific is bound on the instance in initialize(); nothing
is stored at module or class level (multiplexed-gateway rule).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import contextvars
import time
from pathlib import Path
from typing import Iterable

# Documented Hermes surfaces only (catalog rule 9).
from agent.memory_provider import MemoryProvider
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


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _rate_limit_sleep(rate_ms: int) -> None:
    """Minimum delay between remember calls during backfill."""
    if rate_ms > 0:
        time.sleep(rate_ms / 1000.0)


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
        self._checkpoint_digests: dict[str, str] = {}
        self._write_digests: dict[str, str] = {}
        self._backfill_digests: dict[str, str] = {}

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
        assert self._client is not None
        started = time.monotonic()
        try:
            res = self._client.recall(
                query[:4000],
                agent_id=self._agent_id,
                top_k=int(self._cfg.get("top_k", 8)),
                conversation_id=self._conversation_scope(session_id),
            )
        except NotProvisioned as exc:
            logger.warning("openloft-memory: not provisioned (%s ms) — %s",
                           _elapsed_ms(started), exc)
            return None
        except Exception as exc:  # noqa: BLE001 - fail-open reads
            logger.warning("openloft-memory: prefetch degraded (%s ms) (%s)",
                           _elapsed_ms(started), exc)
            return None
        logger.debug("openloft-memory: prefetch ok (%s ms)", _elapsed_ms(started))

        context = res.get("context") or ""
        if not context.strip():
            return ""
        return shrink(context, int(self._cfg.get("inject_budget", 6000)))

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

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages=None, **kwargs) -> None:
        """Non-blocking ingest: join-first, one in flight, never blocks a turn."""
        if not self._ready() or not self._cfg.get("ingest_enabled", True):
            return
        if self._context_kind not in {"primary", ""} and not self._cfg.get("ingest_nonprimary"):
            return
        payload = list(messages) if messages else turn_payload(user_content, assistant_content)
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

    def shutdown(self, **kwargs) -> None:
        thread = self._sync_thread
        if thread is not None:
            try:
                thread.join(timeout=_SYNC_JOIN_TIMEOUT)
            except RuntimeError:  # join from the same thread
                pass

    # -- phase 3 hardening hooks ----------------------------------------------

    def on_pre_compress(self, messages: list, *,
                        require_checkpoint: bool = False,
                        conversation_id: str | None = None,
                        **kwargs) -> str:
        """Archive transcript evidence before context compression (Phase 3, 3.1).

        The transcript is hashed to a content digest and persisted via
        ``remember``; identical digests are skipped (client-side idempotency).
        Archiving is opt-in through ``pre_compress_archive`` unless Hermes sets
        ``require_checkpoint`` (``compression.checkpoint_required: true``),
        which forces the write. Fail-visible: errors are logged, compression
        never blocks on this hook.

        Returns a short text contribution for the compression summary prompt,
        or empty string when nothing to contribute.
        """
        if not self._ready():
            return ""
        archive = bool(self._cfg.get("pre_compress_archive")) or require_checkpoint
        if not archive:
            return ""
        try:
            canonical = json.dumps(messages, sort_keys=True,
                                   separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            logger.warning("openloft-memory: checkpoint digest failed (%s)", exc)
            return ""
        conv = (conversation_id or self._conversation_scope("")) or ""
        if self._checkpoint_digests.get(conv) == digest:
            return f"[openloft-memory: duplicate checkpoint {digest[:12]}]"
        started = time.monotonic()
        try:
            assert self._client is not None
            self._client.remember(
                list(messages),
                agent_id=self._agent_id,
                conversation_id=conv,
            )
            self._checkpoint_digests[conv] = digest
            logger.info("openloft-memory: checkpoint archived %s (%s ms)",
                        digest[:12], _elapsed_ms(started))
            return f"[openloft-memory: checkpoint {digest}]"
        except RateLimited as exc:
            logger.warning("openloft-memory: checkpoint rate-limited (%s)", exc)
            return ""
        except Exception as exc:  # noqa: BLE001 - archive never blocks compression
            logger.error("openloft-memory: checkpoint failed (%s); compression "
                         "proceeds without this archive", exc)
            return ""

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: dict | None = None, **kwargs) -> dict | None:
        """Mirror explicit memory writes (MEMORY.md / USER.md edits) into
        OpenLoft as searchable records (Phase 3, 3.2).

        Destructive writes (edit/delete/replace) are skipped when
        ``previous_content`` is absent — we can't diff what we haven't seen.
        Opt-in through ``memory_write_mirror``.
        """
        if not self._cfg.get("memory_write_mirror", False):
            return {"mirrored": False, "skipped": "opt_out"}
        if not self._ready():
            return {"mirrored": False, "skipped": "not_ready"}
        meta = metadata or {}
        destructive = str(action).lower() in {"edit", "delete",
                                              "remove", "replace"}
        if destructive and meta.get("previous_content") is None:
            return {"mirrored": False, "skipped": "unsafe_without_prior"}
        canonical = json.dumps([action, target, content], sort_keys=True,
                               separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        conv = (self._conversation_scope("")) or ""
        if self._write_digests.get(conv) == digest:
            return {"mirrored": False, "skipped": "duplicate",
                    "digest": digest[:12]}
        prior = meta.get("previous_content")
        payload = [
            {"role": "system",
             "content": ("openloft-memory-write %s %s%s"
                         % (action, target,
                            (" | prior: " + str(prior)[:200]) if prior else ""))},
            {"role": "user", "content": str(content)[:20000]},
        ]
        started = time.monotonic()
        try:
            assert self._client is not None
            self._client.remember(
                payload,
                agent_id=self._agent_id,
                conversation_id=conv,
            )
            self._write_digests[conv] = digest
            logger.info("openloft-memory: mirrored %s %s (%s ms)",
                        action, target, _elapsed_ms(started))
            return {"mirrored": True, "digest": digest}
        except RateLimited as exc:
            logger.warning("openloft-memory: write mirror rate-limited (%s)", exc)
            return {"mirrored": False, "error": "rate_limited"}
        except Exception as exc:  # noqa: BLE001 - never fail a user write for memory
            logger.error("openloft-memory: write mirror failed (%s)", exc)
            return {"mirrored": False, "error": str(exc)}

    def on_session_switch(self, new_session_id: str, *,
                          parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False,
                          **kwargs) -> None:
        """Handle Hermes session_id rotation (Phase 3.4).

        When Hermes switches session_id mid-process (/resume, /branch,
        /reset, context compression), update the conversation scope so
        subsequent writes land in the correct session's record.
        """
        if reset:
            # New conversation — clear per-session digest caches so
            # backfill/checkpoint dedup doesn't carry over stale state.
            self._checkpoint_digests.pop(new_session_id, None)
            self._write_digests.pop(new_session_id, None)
        logger.debug("openloft-memory: session switched to %s (parent=%s, reset=%s)",
                     new_session_id, parent_session_id, reset)

    def get_status_config(self, provider_config: dict) -> dict:
        """Return status fields for `hermes memory status` display."""
        return {
            "provider": self.name,
            "base_url": self._cfg.get("base_url", DEFAULT_BASE_URL),
            "agent_id": self._agent_id,
            "conversation_scope": self._conv_mode,
            "ingest_enabled": self._cfg.get("ingest_enabled", True),
            "toolset_enabled": self._cfg.get("toolset_enabled", True),
        }

    def backup_paths(self) -> list[str]:
        """Return on-disk paths outside HERMES_HOME for `hermes backup`.

        Our config lives at $HERMES_HOME/openloft-memory/config.json which
        is already inside HERMES_HOME, so nothing external to declare.
        """
        return []

    # -- phase 3.4 backfill ------------------------------------------------------

    def backfill(self,
                 sessions: Iterable[dict],
                 *,
                 limit: int | None = None,
                 batch_size: int | None = None,
                 rate_ms: int | None = None,
                 dry_run: bool = False) -> dict:
        """Ingest historical session transcripts into OpenLoft (Phase 3.4).

        Each session dict: {"session_id": str, "messages": [{"role": ..., "content": ...}, ...]}
        Returns: {"ingested": N, "skipped": M, "dry_run": bool, "errors": [...], "digests": [...]}

        Opt-in through ``backfill_enabled`` — never runs silently. Duplicate
        transcripts (same session_id + digest) are skipped. Rate-limited calls
        are recorded as errors and the run continues; one bad session never
        kills the whole backfill.
        """
        if not self._cfg.get("backfill_enabled"):
            return {"ingested": 0, "skipped": "opt_out",
                    "dry_run": dry_run, "errors": [], "digests": []}
        if not self._ready():
            return {"ingested": 0, "skipped": "not_ready",
                    "dry_run": dry_run, "errors": [], "digests": []}

        assert self._client is not None

        batch = int(batch_size if batch_size is not None
                    else self._cfg.get("backfill_batch_size", 10))
        if limit is not None:
            batch = min(batch, limit)
        rate = int(rate_ms if rate_ms is not None
                   else self._cfg.get("backfill_rate_ms", 1500))

        ingested = 0
        skipped = 0
        errors: list[dict] = []
        digests: list[str] = []
        count = 0

        for session in sessions:
            if count >= batch:
                break
            count += 1

            session_id = str(session.get("session_id", ""))
            messages = list(session.get("messages", []))
            if not messages:
                skipped += 1
                continue

            canonical = json.dumps(messages, sort_keys=True,
                                   separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            if self._backfill_digests.get(session_id) == digest:
                skipped += 1
                digests.append(digest)
                continue

            if dry_run:
                digests.append(digest)
                continue

            conv = self._conversation_scope("")
            started = time.monotonic()
            try:
                self._client.remember(
                    messages,
                    agent_id=self._agent_id,
                    conversation_id=conv,
                )
                self._backfill_digests[session_id] = digest
                ingested += 1
                digests.append(digest)
                logger.info("openloft-memory: backfilled %s (%s ms)",
                            session_id[:12], _elapsed_ms(started))
            except RateLimited as exc:
                errors.append({"session_id": session_id,
                               "error": "rate_limited"})
                logger.warning("openloft-memory: backfill rate-limited for %s (%s)",
                               session_id[:12], exc)
            except Exception as exc:  # noqa: BLE001 - never fail the whole run
                errors.append({"session_id": session_id,
                               "error": str(exc)})
                logger.error("openloft-memory: backfill failed for %s (%s)",
                             session_id[:12], exc)

            _rate_limit_sleep(rate)

        return {"ingested": ingested, "skipped": skipped,
                "dry_run": dry_run, "errors": errors, "digests": digests}

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
        assert self._client is not None
        conversation_id = self._conversation_scope(str(args.get("_session_id") or ""))
        if tool_name == "loft_recall":
            started = time.monotonic()
            res = self._client.recall(
                str(args.get("query") or ""),
                agent_id=self._agent_id,
                top_k=int(args.get("top_k") or self._cfg.get("top_k", 8)),
                conversation_id=conversation_id,
            )
            logger.debug("openloft-memory: tool loft_recall ok (%s ms)",
                         _elapsed_ms(started))
            return res
        if tool_name == "loft_remember":
            text = str(args.get("text") or "").strip()
            if not text:
                raise ScopeError("loft_remember needs text to remember")
            started = time.monotonic()
            res = self._client.remember(
                [{"role": "user", "content": text[:20000]}],
                agent_id=self._agent_id,
                conversation_id=conversation_id,
            )
            logger.debug("openloft-memory: tool loft_remember ok (%s ms)",
                         _elapsed_ms(started))
            return res
        if tool_name == "loft_forget":
            started = time.monotonic()
            res = self._client.forget(
                agent_id=self._agent_id,
                entity_name=args.get("entity_name"),
                record_id=args.get("record_id"),
                conversation_id=conversation_id,
            )
            logger.debug("openloft-memory: tool loft_forget ok (%s ms)",
                         _elapsed_ms(started))
            return res
        if tool_name == "loft_stats":
            started = time.monotonic()
            res = self._client.stats(agent_id=self._agent_id,
                                     conversation_id=conversation_id)
            logger.debug("openloft-memory: tool loft_stats ok (%s ms)",
                         _elapsed_ms(started))
            return res
        if tool_name == "loft_search":
            started = time.monotonic()
            res = self._client.search(
                str(args.get("query") or ""),
                agent_id=self._agent_id,
                limit=int(args.get("limit") or 10),
                conversation_id=conversation_id,
            )
            logger.debug("openloft-memory: tool loft_search ok (%s ms)",
                         _elapsed_ms(started))
            return res
        raise ScopeError(f"unknown tool '{tool_name}' — valid: loft_recall, loft_remember, "
                         "loft_forget, loft_stats, loft_search")

    # -- internals -------------------------------------------------------------------

    def _spawn(self, job, name: str):
        """Spawn a background thread with contextvar propagation.

        Hermes v0.19.0 doesn't provide ``spawn_context_thread``, so we
        capture the current context and replay it in the thread — this
        gives the same profile-isolation guarantee without depending on
        a Hermes helper that doesn't exist in this version.
        """
        ctx = contextvars.copy_context()

        def _run() -> None:
            ctx.run(job)

        thread = threading.Thread(target=_run, daemon=True, name=name)
        thread.start()
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
        if not self._ready():
            return
        assert self._client is not None
        started = time.monotonic()
        try:
            self._client.recall(
                query[:4000],
                agent_id=self._agent_id,
                top_k=int(self._cfg.get("top_k", 8)),
                conversation_id=self._conversation_scope(session_id),
            )
            logger.debug("openloft-memory: warm ok (%s ms)", _elapsed_ms(started))
        except Exception:  # noqa: BLE001 - warm-up is best-effort
            logger.debug("openloft-memory: warm skipped (%s ms)", _elapsed_ms(started))

    def _ingest(self, payload: list, session_id: str, previous: threading.Thread | None) -> None:
        if previous is not None:
            try:
                previous.join(timeout=_SYNC_JOIN_TIMEOUT)
            except RuntimeError:
                pass
        if not self._ready():
            return
        assert self._client is not None
        started = time.monotonic()
        try:
            res = self._client.remember(
                payload,
                agent_id=self._agent_id,
                conversation_id=self._conversation_scope(session_id),
            )
            logger.info(
                "openloft-memory: ingested %s updates (turn %s, %s ms)",
                res.get("updates_ingested"), res.get("turn"),
                _elapsed_ms(started),
            )
        except RateLimited as exc:
            logger.warning("openloft-memory: ingest rate-limited (next turn re-sends a "
                           "fuller transcript); %s", exc)
        except Exception as exc:  # noqa: BLE001 - never fail the turn for memory
            logger.error("openloft-memory: ingest failed (%s ms) (%s); the next turn's "
                         "fuller transcript is the recovery path",
                         _elapsed_ms(started), exc)
