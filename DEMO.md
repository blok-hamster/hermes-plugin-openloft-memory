# OpenLoft Memory Plugin — Hackathon Demo Guide

90-second live demo showing the plugin installed, memory active, and the remember → recall → forget lifecycle.

## Prerequisites

Set both API keys in your Hermes environment:

```bash
export HERMES_HOME="$HOME/.hermes"
echo "OPENROUTER_API_KEY=sk-or-v1-..." >> "$HERMES_HOME/.env"
echo "OPENLOFT_MEMORY_API_KEY=nmafc_<tenant>.<secret>" >> "$HERMES_HOME/.env"
chmod 600 "$HERMES_HOME/.env"
```

The OpenLoft key is minted from the dashboard: `https://www.openloft.xyz/memory/connections`

---

## Demo Steps

Run these commands in order. Each step shows a specific part of the plugin lifecycle.

### Step 1: Plugin is installed and active

```bash
hermes memory status
```

**Expected output:**
```
Provider:  openloft-memory  ← active
Plugin:    installed ✓
Status:    available ✓
```

**Say:** "The OpenLoft Memory plugin is installed and active in Hermes."

---

### Step 2: Memory backend is reachable

```bash
hermes openloft-memory health
```

**Expected output:**
```
provisioned · backend reachable · turn 0
```

**Say:** "The memory backend is provisioned and reachable."

---

### Step 3: Store a fact (TUI)

```bash
hermes --tui chat
```

Inside the TUI, type:

```
remember I prefer dark mode
```

**Expected:** Agent says something like "Noted — your preference for dark mode has been saved."

Press `q` or `Ctrl-C` to exit the TUI.

**Say:** "I'm storing a fact in long-term memory."

---

### Step 4: Recall the fact (TUI)

```bash
hermes --tui chat
```

Inside the TUI, type:

```
what do I prefer?
```

**Expected:** Agent says "You prefer dark mode."

Press `q` or `Ctrl-C` to exit the TUI.

**Say:** "The agent recalled the fact from long-term memory."

---

### Step 5: Forget the fact (temporal invalidation)

```bash
hermes openloft-memory forget user_preference_dark_mode --yes
```

**Expected output:**
```
Forget = TEMPORAL INVALIDATION. The fact stops being retrieved;
the write-ahead log retains it. This is not erasure.
{
  "tenant": "org_f25ccaa9",
  "agent_id": "hermes",
  "forgotten": 1,
  "by": "entity_name",
  "semantics": "temporal_invalidation"
}
```

**Say:** "I'm forgetting the fact. Note it says 'temporal_invalidation' — this is not erasure."

---

### Step 6: Prove the fact is gone (TUI)

```bash
hermes --tui chat
```

Inside the TUI, type:

```
what do I prefer?
```

**Expected:** Agent says it doesn't know (e.g., "I don't have that information")

Press `q` or `Ctrl-C` to exit the TUI.

**Say:** "The fact is no longer retrieved — it's been invalidated, not erased."

---

### Step 7: Show final stats (optional)

```bash
hermes openloft-memory stats
```

**Expected output:** Shows `turn` advanced, `hot` count with records.

**Say:** "The store shows the full lifecycle — the write happened, then the invalidation."

---

## Agent ID & Conversation ID

These two values determine where memories are stored.

| Setting | Default | What it does |
|---|---|---|
| `agent_id` | `auto` | Resolves to: config → `OPENLOFT_MEMORY_AGENT_ID` env → Hermes profile name → `hermes`. One agent id = one isolated memory pool. |
| `conversation_scope` | `agent` | `agent` (recommended): one pool per agent, immune to session rotation. `session`: one pool per Hermes session. `shared`: tenant-wide store visible to all agents. |

**Change agent_id:**
```bash
export OPENLOFT_MEMORY_AGENT_ID="my-agent"
# or
hermes memory setup   # select openloft-memory, set agent_id
```

**Change conversation_scope:**
```bash
hermes memory_setup   # select openloft-memory, set conversation_scope
```

**Defaults:** `agent_id` = `hermes`, `conversation_scope` = `agent`. Works out of the box for single-agent setups.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `hermes memory status` doesn't show openloft-memory | Run `hermes plugins enable openloft-memory` |
| `health` says not provisioned | Check `OPENLOFT_MEMORY_API_KEY` is in `~/.hermes/.env` |
| `forget` says key rejected | Check `OPENLOFT_MEMORY_API_KEY` is set in current shell |
| Recall returns empty | Fact may not exist yet — run step 3 first |

---

## License

MIT — see `LICENSE`.
