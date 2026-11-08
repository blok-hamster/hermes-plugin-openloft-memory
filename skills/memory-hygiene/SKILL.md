---
name: memory-hygiene
description: How to curate the OpenLoft memory store via the loft_* tools — when to remember, how to forget, and what decay means.
---

# Caring for your long-term memory

Your memory lives in OpenLoft Memory. Every turn is ingested automatically
server-side; recall is injected before your turns. Use the tools to curate.

## When to use `loft_remember`

- The user states a durable preference, constraint, or identity fact
  ("always answer in German", "our staging port is 8123").
- You are told to remember something explicitly.
- Don't stockpile trivia: every remembered fact costs one extraction call and
  occupies the store. Automatic ingestion already covers conversation content.

## When to use `loft_forget`

- A fact is wrong, superseded, or the user asks you to stop using it.
- **Forget is temporal invalidation, not deletion** — the record stops being
  retrieved but is retained. Never claim data was erased.

## Fading is a feature

Facts decay on a curve unless reinforced. When the user asks why something is
"forgotten", explain decay honestly, then re-run `loft_remember` if they want
it back.

## Adversarial questions

On questions whose answer is not in the store, say "I don't have that in
memory" — do not invent. If recall returns nothing useful, say so; the store
may simply not contain it.
