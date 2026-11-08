"""OpenLoft Memory service client — stdlib only.

The client speaks to a configured OpenLoft memory backend with a
``Authorization: Bearer <nmafc key>`` credential and snake_case bodies on both
supported endpoints:

- hosted edge:      https://api.openloft.xyz/api/memory   (accepts our dialect)
- self-hosted:      http://<host>:8100/v1/memory          (native dialect)

Contract decisions pinned in ../HERMES_MEMORY_PLUGIN_PLAN.md §3.6:

- one retry on connect failure for reads only; never retry a 5xx write
  (the substrate may have persisted — the next turn's fuller ingest is the
  recovery path);
- errors are classified, not swallowed: AuthError / NotProvisioned /
  RateLimited / ScopeError / ServiceError;
- no dependencies: everything here is importable on any Python 3.11+.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .scope import SCOPE_RE

CONNECT_RETRIES_READS = 1
CONNECT_RETRY_WAIT = 0.4

DEFAULT_TIMEOUTS = {
    "recall": 8,
    "remember": 120,   # extraction is a server-side LLM call
    "stats": 5,
    "search": 10,
    "forget": 20,
}


class ServiceError(Exception):
    """Base class for every taxonomy branch the provider switches on."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(ServiceError):
    """The key is absent, malformed, or not accepted (401/403)."""


class NotProvisioned(ServiceError):
    """The tenant behind this key has no memory store yet (503)."""


class RateLimited(ServiceError):
    """The backend answered 429; surface, wait politely, or back off."""


class ScopeError(ServiceError):
    """Bad sub-scope (400) — most often the sanitizer failed to run."""


class ServiceErrorCatchall(ServiceError):
    """Anything else the backend returns."""


class ServiceClient:
    """Thin bearer-token client around urllib with the plan's retry policy."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.default_timeout = timeout

    # -- plumbing -----------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        """One HTTP call with the plan's retry policy. Returns parsed JSON."""
        url = f"{self.base_url}{path}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        attempt = 0
        while True:
            attempt += 1
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method=method,
                )
                with urllib.request.urlopen(req, timeout=timeout or self.default_timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raise self._classify(exc) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                read_only = method in {"GET", "HEAD"}
                if read_only and attempt <= CONNECT_RETRIES_READS:
                    time.sleep(CONNECT_RETRY_WAIT * attempt)
                    continue
                raise ServiceErrorCatchall(f"memory backend unreachable: {exc}") from None

    @staticmethod
    def _classify(exc: urllib.error.HTTPError) -> ServiceError:
        status = exc.code
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("detail", "")
        except Exception:  # noqa: BLE001 - body may be a plain error page
            detail = ""
        if status in (401, 403):
            return AuthError(detail or "key rejected", status=status)
        if status == 503 and "provision" in str(detail).lower():
            return NotProvisioned(str(detail) or "tenant not provisioned", status=status)
        if status == 503:
            return ServiceErrorCatchall(str(detail) or "backend unavailable", status=status)
        if status == 429:
            return RateLimited(detail or "rate limited", status=status)
        if status == 400:
            return ScopeError(str(detail) or "invalid sub-scope", status=status)
        return ServiceErrorCatchall(f"memory backend error {status}: {detail}", status=status)

    # -- substrate operations (plan §3.4) -----------------------------------

    def recall(self, query: str, *, agent_id: str, top_k: int = 8, conversation_id: str | None = None):
        """Read-only retrieval. No writes, no reinforcement — safe to poll."""
        return self.request(
            "POST", "/recall",
            body=_drop_none({"query": query, "top_k": top_k,
                              "agent_id": agent_id, "conversation_id": conversation_id}),
            timeout=DEFAULT_TIMEOUTS["recall"],
        )

    def remember(self, messages: list, *, agent_id: str, conversation_id: str | None = None):
        """Ingest a transcript; costs one server-side extraction call."""
        return self.request(
            "POST", "/remember",
            body=_drop_none({"messages": messages, "agent_id": agent_id,
                              "conversation_id": conversation_id}),
            timeout=DEFAULT_TIMEOUTS["remember"],
        )

    def forget(self, *, agent_id: str, entity_name: str | None = None,
               record_id: str | None = None, conversation_id: str | None = None):
        """Temporal invalidation, not erasure."""
        payload = _drop_none({"agent_id": agent_id, "conversation_id": conversation_id,
                               "entity_name": entity_name, "record_id": record_id})
        if ("entity_name" in payload) == ("record_id" in payload):
            raise ScopeError("forget needs exactly one of entity_name / record_id")
        # The substrate allowlists these values on its GET record paths but not
        # on forget itself; the engine splices record ids into a shared-table
        # predicate, so anything outside the scope charset is refused HERE.
        for key in ("entity_name", "record_id"):
            if key in payload and not SCOPE_RE.match(str(payload[key])):
                raise ScopeError(f"{key} may contain only letters, digits, '-' and '_'")
        return self.request("POST", "/forget", body=payload, timeout=DEFAULT_TIMEOUTS["forget"])

    def stats(self, *, agent_id: str, conversation_id: str | None = None):
        counts = _drop_none({"agent_id": agent_id, "conversation_id": conversation_id})
        suffix = f"?{urllib.parse.urlencode(counts)}" if counts else ""
        return self.request("GET", f"/stats{suffix}", timeout=DEFAULT_TIMEOUTS["stats"])

    def search(self, query: str, *, agent_id: str, limit: int = 10,
               conversation_id: str | None = None):
        return self.request(
            "POST", "/records/search",
            body=_drop_none({"query": query, "limit": limit, "agent_id": agent_id,
                              "conversation_id": conversation_id}),
            timeout=DEFAULT_TIMEOUTS["search"],
        )


def _drop_none(payload: dict) -> dict:
    """The substrate ignores absent fields; None must never be serialized."""
    return {k: v for k, v in payload.items() if v is not None}
