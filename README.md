# OpenLoft Memory — a Hermes memory provider plugin

Persistent, graph-backed long-term memory for **any** [Hermes Agent](https://hermes-agent.nousresearch.com)
deployment, backed by OpenLoft Memory. Recall is injected before every turn,
turns are ingested after every turn, and the agent gets five `loft_*` memory
tools. Works with the hosted service or a self-hosted memory backend.

> **Status: Phase 1 scaffold.** Structure and contracts are in place; see
> `../HERMES_MEMORY_PLUGIN_PLAN.md` for the full phased plan.

## What the agent gets

| Capability | What it does |
|---|---|
| Automatic recall | Relevant memory injected as context before every LLM turn |
| Automatic ingest | Every completed turn is persisted (extraction happens server-side) |
| `loft_recall(query)` | On-demand read-only retrieval |
| `loft_remember(text)` | Explicit curation ("remember this preference") |
| `loft_forget(entity_or_id)` | **Temporal invalidation** — the fact stops being retrieved; it is not erased |
| `loft_stats()` | Turn number, hot/cold/event counts for this agent's store |
| `loft_search(query)` | Scored vector search over stored records |

Only one external memory provider may be active per agent (`memory.provider`).

## Quickstart

```bash
# 1. Mint a key
#    Hosted: dashboard → Memory → Connections → mint (shown exactly once)
#    Self-hosted:
#      python -c "from app.auth import generate_key; print(generate_key('my_tenant'))"

# 2. Install (drop-in — the catalog path comes post-review)
git clone https://github.com/OWNER/hermes-plugin-openloft-memory \
      "$HOME/.hermes/plugins/openloft-memory"

# 3. Enable + verify
hermes plugins enable openloft-memory    # prompts for OPENLOFT_MEMORY_API_KEY
hermes openloft-memory health

# 4. Chat — memory is on
hermes
> remember that I prefer dark mode
```

## Configuration

Set with `hermes memory setup`, the Desktop plugins panel, or
`$HERMES_HOME/openloft-memory.json`. The key itself is a secret: it is stored
as `OPENLOFT_MEMORY_API_KEY` in the profile `.env` and read at runtime through
`agent.secret_scope.get_secret()` — never from raw environment variables.

| Key | Default | Purpose |
|---|---|---|
| `base_url` | `https://api.openloft.xyz/api/memory` | Hosted edge. Self-hosted: `http://<host>:8100/v1/memory` |
| `api_key` | — | `nmafc_<tenant>.<secret>`; the key carries the tenant |
| `agent_id` | `auto` | `auto` → env override → Hermes profile name → `hermes` |
| `conversation_scope` | `agent` | `agent` \| `session` \| `shared` |
| `top_k` | `8` | Recall breadth |
| `inject_budget` | `6000` | Max chars injected per turn |
| `ingest_enabled` | `true` | Persist turns |
| `toolset_enabled` | `true` | Expose `loft_*` tools |
| `warm_next_turn` | `true` | Keep the backend's store instance warm |

## Disclosure

- The plugin makes HTTPS calls to the configured memory backend (by default
  the hosted OpenLoft service).
- Conversation content is sent to that backend for extraction and storage.
- The API key is a stored credential; treat it like a password.
- The hosted service meters usage per tenant (server-side; the plugin sends no
  telemetry of its own).

## Development

```bash
python3 -m pip install pytest
python3 -m pytest tests/ -q        # stdlib-only suite; hermes-dependent tests skip
hermes plugins doctor . --ci       # inside a Hermes environment
```

Contract suite against the REAL memory service (boots it right here with
deterministic provider fakes at the engine boundary — no model downloads, no
keys in your tree):

```bash
# runs the service.vendored suite: field dialect, tenant isolation, sub-scopes
../backend/memory-service/.venv-test/bin/python scripts/run_contract_suite.py
# options: --port 8123 --watch   (keep serving to curl by hand)
```

## License

MIT — see `LICENSE`.
