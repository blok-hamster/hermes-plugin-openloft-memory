# OpenLoft Memory — a Hermes memory provider plugin

Persistent, graph-backed long-term memory for **any** [Hermes Agent](https://hermes-agent.nousresearch.com)
deployment, backed by OpenLoft Memory. Recall is injected before every turn,
turns are ingested after every turn, and the agent gets five `loft_*` memory
tools. Works with the hosted service or a self-hosted memory backend.

Zero Python dependencies — the client is pure stdlib, so the plugin installs
with no dependency consent step anywhere.

## What the agent gets

| Capability | What it does |
|---|---|
| Automatic recall | Relevant memory injected as context before every LLM turn (fail-open: an unavailable backend degrades to "no memories", never a failed turn) |
| Automatic ingest | Every completed turn is persisted on a background thread; extraction runs server-side; at most one ingest in flight, queued turns never block the agent |
| `loft_recall(query)` | On-demand read-only retrieval |
| `loft_remember(text)` | Explicit curation ("remember that I prefer dark mode") |
| `loft_forget(entity_or_id)` | **Temporal invalidation** — the fact stops being retrieved; the write-ahead log retains it. This is **not** erasure |
| `loft_stats()` | Turn number plus hot/cold/event counts for this agent's store |
| `loft_search(query)` | Scored vector search over stored records |

Facts decay on a curve and consolidation folds duplicates server-side — fading
is a designed property (OpenLoft's NM-AFC engine), not data loss.

Only one external memory provider may be active per agent (`memory.provider`).

## Install

```bash
# drop-in install (catalog listing is a separate human-reviewed track)
git clone https://github.com/blok-hamster/hermes-plugin-openloft-memory \
      "$HOME/.hermes/plugins/openloft-memory"

hermes plugins enable openloft-memory   # prompts for OPENLOFT_MEMORY_API_KEY
hermes openloft-memory health
```

Get a key first:

- **Hosted**: OpenLoft dashboard → Memory → Connections → mint. The secret is
  shown exactly once. (`https://www.openloft.xyz/memory/connections`)
  Check the service is live for your account before first use.
- **Self-hosted**: run the OpenLoft memory service, then mint from the same
  dashboard when it is wired through the platform backend, or generate a
  service key directly:

  ```bash
  python -c "from app.auth import generate_key; print(generate_key('my_tenant'))"
  ```

The key embeds the tenant (`nmafc_<tenant>.<secret>`); nothing a request header
says can switch tenants after that.

Then select the provider:

```bash
hermes memory setup        # or the Desktop → Capabilities → Memory panel
```

## Configuration

Stored as the plugin's own flat JSON (the same file the Desktop panel edits):
`$HERMES_HOME/openloft-memory/config.json`. The key itself is a secret: it is
stored as `OPENLOFT_MEMORY_API_KEY` in the profile `.env` and read at runtime
through `agent.secret_scope.get_secret()` — never from raw environment
variables, so profile isolation holds under multiplexed gateways.

| Key | Default | Purpose |
|---|---|---|
| `base_url` | `https://api.openloft.xyz/api/memory` | Hosted edge. Self-hosted: `http://<host>:8100/v1/memory` |
| `api_key` | — | `nmafc_<tenant>.<secret>`; the key carries the tenant |
| `agent_id` | `auto` | `auto` → `OPENLOFT_MEMORY_AGENT_ID` env → Hermes profile name → `hermes`. One agent id = one memory pool |
| `conversation_scope` | `agent` | `agent` (recommended, survives session-id rotation) \| `session` (one pool per Hermes session — fragments when sessions rotate) \| `shared` (the tenant's pre-agent store) |
| `top_k` | `8` | Recall breadth |
| `inject_budget` | `6000` | Max chars of recalled context injected per turn |
| `ingest_enabled` | `true` | Persist turns. Non-primary agent contexts (cron, subagents) are skipped unless `ingest_nonprimary` is set |
| `toolset_enabled` | `true` | Expose the `loft_*` tools |
| `warm_next_turn` | `true` | Cheap background recall after each turn to keep the backend's store instance warm |

## Self-hosting

Point `base_url` at your memory service (`http://<host>:8100/v1/memory`) and
mint keys per tenant. One caveat for self-hosted operators: the service's
`POST /forget` currently passes `record_id`/`entity_name` to the engine without
the charset allowlist its `GET /records/{id}` routes use — this plugin's client
refuses values outside `[A-Za-z0-9_-]` before the wires as defense-in-depth.
Patching the route to call `_path_value()` closes it server-side (see the
memory service repo's `INTEGRATION.md`, open item 6).

## Disclosure

Things you would want to know before installing:

- **Network calls**: every memory operation talks HTTPS to your configured
  backend — the hosted OpenLoft service by default (`api.openloft.xyz`), or
  your own server. No other third-party endpoints are contacted.
- **Conversation content leaves the machine**: completed turns (user and
  assistant text; Hermes may pass tool calls and tool results in the same
  turn's transcript) are sent to that backend for extraction and storage. If
  your workspace content appears in tool output, it can appear in memory.
  Set `ingest_enabled: false` to keep writes off while keeping the tools.
- **Stored credential**: `OPENLOFT_MEMORY_API_KEY` is stored via Hermes and
  treated like a password.
- **Usage metering**: the hosted service meters usage per tenant, server-side.
  The plugin sends no telemetry of its own.
- **Forgetting is invalidation, not deletion**: `loft_forget` and future
  retention semantics keep server-side logs; retention arises from decay.

## Design notes

- **Reads fail open, writes fail visible.** A recall that hits an unreachable
  backend returns nothing (your turn continues without injected memory — every
  error is logged). Tool calls and ingest report their errors so you can act.
- **The recall budget is bounded**: per-socket timeouts (`recall` 8s) and a
  6,000-char injection cap, head/tail-trimmed.
- **Profile isolation by construction**: every client/store binding is made in
  `initialize()` per instance; background threads are spawned through
  Hermes' `spawn_context_thread` (contextvars-bound), never a bare thread.
- **Identity mapping works anywhere**: agent id resolves from config → env →
  the active Hermes profile name → `hermes`; conversation ids are sanitized
  into the substrate's `[A-Za-z0-9_-]` charset with a None-omission for the
  default pool.

## Development

```bash
python3 -m pip install pytest        # stdlib-only suite; hermes-dependent tests skip
python3 -m pytest tests/ -q

# inside a Hermes environment:
hermes plugins doctor . --ci
hermes plugins validate . --install-deps    # the same check catalog CI runs

# contract suite against the REAL memory service (boots it right here with
# deterministic provider fakes at the engine boundary — no model downloads,
# no keys in your tree):
../backend/memory-service/.venv-test/bin/python scripts/run_contract_suite.py
# options: --port 8123 --watch   (keep serving to curl by hand)
```

## License

MIT — see `LICENSE`.
