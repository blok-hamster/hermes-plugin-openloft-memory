"""Scope helpers — generic identity mapping for the OpenLoft memory substrate.

The substrate constrains both ``agent_id`` and ``conversation_id`` to
``[A-Za-z0-9_-]{1,128}``. Hermes session keys do not respect that charset and
Hermes session ids can rotate mid-conversation (compression), so this module is
where every identity gets sanitized and resolved. It is deliberately free of
Hermes imports so it is unit-testable on any box.
"""

from __future__ import annotations

import os
import re

# The substrate's own validation constraint (app/tenancy.py).
SCOPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Keep room for the truncation suffix inside the 128-char cap.
_SCOPE_MAX = 96


def conforms(value: str) -> bool:
    """True when a scope string would be accepted by the substrate as-is."""
    return bool(SCOPE_RE.match(value or ""))


def sanitize(value: str, *, max_len: int = _SCOPE_MAX) -> str:
    """Force a string into the substrate's scope charset.

    Foreign bytes become "-", runs of separators collapse, and truncation
    appends a short random suffix so two long identities can never collapse
    onto the same store name.
    """
    cleaned = "-".join(p for p in re.split(r"[^A-Za-z0-9_-]+", (value or "").strip()) if p)
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:max_len]
    if not cleaned:
        return "default"
    if len(value) > max_len:
        cleaned = f"{cleaned[:max_len]}-{os.urandom(2).hex()}"
    return cleaned


def resolve_agent_id(
    cfg_override: str | None = None,
    env_id: str | None = None,
    profile_identity: str = "",
    fallback: str = "hermes",
) -> str:
    """Work out which agent store in the tenant this Hermes instance is.

    Resolution order (every source exists on any install — no OpenLoft
    environment variables are required):

    1. ``agent_id`` from user config (memory setup / Desktop panel / JSON)
    2. ``OPENLOFT_MEMORY_AGENT_ID`` env override
    3. the active Hermes profile name (``agent_identity`` kwarg)
    4. fixed fallback for single-profile default installs
    """
    for source in (cfg_override, env_id, profile_identity):
        value = (source or "").strip()
        if value and value.lower() != "auto":
            return sanitize(value)
    return sanitize(fallback)


def resolve_conversation_id(mode: str, session_id: str = "") -> str | None:
    """Return the body ``conversation_id`` for a scope mode.

    ``None`` means "omit the field": the substrate serves the agent store's
    default conversation, immune to Hermes session-id rotation.
    """
    mode = (mode or "agent").strip().lower()
    if mode == "session":
        return sanitize(session_id or "default")
    if mode in {"agent", "shared", ""}:
        return None
    return None
