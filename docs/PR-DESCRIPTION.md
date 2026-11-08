# Catalog PR: openloft-memory memory provider plugin

## What this adds

`plugin-catalog/openloft-memory.yaml` — a Memory-Provider plugin entry for
**OpenLoft Memory** (`openloft-memory`), a graph-backed long-term memory
provider for any Hermes Agent deployment (hosted default edge or self-hosted
`base_url`).

## What the plugin does

| Surface | Behavior |
|---|---|
| Recall | Injected before every LLM turn via `prefetch`; fail-open, budgeted (6,000-char injection cap, 8s socket timeout) |
| Ingest | Every completed turn persisted in the background via `sync_turn` (`spawn_context_thread`, join-first, single-in-flight) |
| Tools | `loft_recall`, `loft_remember`, `loft_forget`, `loft_stats`, `loft_search` (JSON-always handlers) |
| Identity | Config → `OPENLOFT_MEMORY_AGENT_ID` → Hermes profile name → `hermes`; one pool per agent by default. Configurable via `agent_id` config key or `OPENLOFT_MEMORY_AGENT_ID` env var. Conversation scope defaults to `agent` (one pool per agent, immune to session rotation); `session` and `shared` are opt-in. |
| Backend | Hosted `https://api.openloft.xyz/api/memory` or self-hosted `http://<host>:8100/v1/memory`; snake_case bodies always |

## Hermes surfaces used (documented contract only)

- `agent.memory_provider.MemoryProvider` ABC (`prefetch`, `queue_prefetch`,
  `sync_turn`, `system_prompt_block`, `on_session_end`, `shutdown`,
  `handle_tool_call`, `get_config_schema`, `get_tool_schemas`, `name`,
  `is_available`, `initialize`, `save_config`)
- `agent.secret_scope.get_secret("OPENLOFT_MEMORY_API_KEY")` — the key is
  **never** read from raw `os.environ` (catalog rule 11)
- `spawn_context_thread` for background ingest threads (contextvars-bound)
- `register_memory_provider()` and `register_skill()` in `register(ctx)`
- Phase 3 hooks: `on_pre_compress(messages, *, require_checkpoint=False)` and
  `on_memory_write(action, target, content, metadata=None)`

## Rule-13 disclosure (verbatim from README)

- **Network calls**: every memory operation talks HTTPS to the configured
  backend — the hosted OpenLoft service by default, or your own server. No
  other third-party endpoints are contacted.
- **Conversation content leaves the machine**: completed turns (user and
  assistant text; Hermes may pass tool calls and tool results in the same
  turn's transcript) are sent to that backend for extraction and storage.
  Set `ingest_enabled: false` to keep writes off while keeping the tools.
- **Stored credential**: `OPENLOFT_MEMORY_API_KEY` is stored via Hermes and
  treated like a password.
- **Usage metering**: the hosted service meters usage per tenant,
  server-side. The plugin sends no telemetry of its own.
- **Forgetting is invalidation, not deletion**: `loft_forget` is temporal
  invalidation; the write-ahead log retains the fact.

## Adoption paths

- **Drop-in (hackathon / judges):** `git clone` the repo into the plugins dir,
  `hermes plugins enable openloft-memory`, mint a key, `hermes memory setup`.
- **Catalog:** `hermes plugins install openloft-memory` — clones the repo and
  checks out the pinned SHA `e5532491b94d573e3809cca6f73e1dbfc2467045`.

## Compliance notes

- Zero Python dependencies (stdlib-only client)
- No self-updater code (catalog rule 3)
- No core overrides, no monkey-patching (rule 9)
- `requires_hermes: ">=0.19.0"` — measured on Hermes v0.19.0; plugin loads,
  registers, and passes full E2E on that version. Declared identically in
  `plugin.yaml` and this entry (rule 14).
