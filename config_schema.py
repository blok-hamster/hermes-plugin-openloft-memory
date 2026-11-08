"""Configuration field definitions — shared by the memory-setup wizard,
the Desktop panel (``config_schema.py`` convention) and the runtime defaults.

Only the keys a user actually must touch live here; everything else is
documented in README.md (plan §2, "keep the schema minimal").
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_BASE_URL = "https://api.openloft.xyz/api/memory"

# Panel + runtime use ONE file (Desktop STORAGE_FLAT_JSON convention:
# $HERMES_HOME/<name>/config.json), so dashboard edits and the provider's
# runtime binding can never diverge.
CONFIG_DIR_NAME = "openloft-memory"


def runtime_config_path(hermes_home: str | None) -> Path:
    return Path(hermes_home or "") / CONFIG_DIR_NAME / "config.json"

FIELDS = [
    {
        "key": "base_url",
        "description": "OpenLoft memory backend. Hosted edge by default; "
                       "self-hosted is e.g. http://localhost:8100/v1/memory",
        "default": DEFAULT_BASE_URL,
        "required": False,
    },
    {
        "key": "api_key",
        "description": "OpenLoft memory key (nmafc_<tenant>.<secret>)",
        "secret": True,
        "required": True,
        "env_var": "OPENLOFT_MEMORY_API_KEY",
        "url": "https://app.openloft.xyz/dashboard/memory/connections",
    },
    {
        "key": "agent_id",
        "description": "Agent store inside the tenant. 'auto' = env override, "
                       "then the Hermes profile name, then 'hermes'.",
        "default": "auto",
        "required": False,
    },
    {
        "key": "conversation_scope",
        "description": "agent = one pool per agent (recommended); "
                       "session = per Hermes session (fragments on rotation); "
                       "shared = the tenant's shared pre-agent store",
        "default": "agent",
        "choices": ["agent", "session", "shared"],
        "required": False,
    },
    {
        "key": "top_k",
        "description": "How many facts a recall pulls",
        "default": 8,
    },
    {
        "key": "inject_budget",
        "description": "Max characters of recalled context injected per turn",
        "default": 6000,
    },
    {
        "key": "ingest_enabled",
        "description": "Persist completed turns into memory",
        "default": True,
    },
    {
        "key": "toolset_enabled",
        "description": "Expose the loft_* memory tools to the model",
        "default": True,
    },
    {
        "key": "warm_next_turn",
        "description": "Cheap background recall after each turn to keep the "
                       "backend's store instance warm",
        "default": True,
    },
    {
        "key": "pre_compress_archive",
        "description": "Archive transcript evidence before context compression "
                       "(Phase 3 — fail-closed under compression.checkpoint_required)",
        "default": False,
    },
]

RUNTIME_DEFAULTS = {
    f["key"]: f.get("default")
    for f in FIELDS
    if not f.get("secret")   # secrets are resolved exclusively via get_secret
}


def runtime_config(profile_json: dict | None, hermes_home: str | None = None) -> dict:
    """Merge schema defaults under a user-editable runtime config file.

    The file is the provider's own flat JSON,
    ``$HERMES_HOME/openloft-memory/config.json`` (the Desktop panel's flat JSON,
    written atomically by ``save_config`` / edited by users). Secrets never come
    ``save_config`` / edited by users). Secrets never come back from here —
    the key is resolved separately through
    ``agent.secret_scope.get_secret("OPENLOFT_MEMORY_API_KEY")``.
    """
    merged = dict(RUNTIME_DEFAULTS)
    user_values = (profile_json or {})
    for key in merged:
        if key in user_values:
            merged[key] = user_values[key]
    if hermes_home:
        merged["_hermes_home"] = hermes_home
    return merged
