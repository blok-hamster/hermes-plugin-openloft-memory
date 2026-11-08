"""Pure provider wiring — tool schemas, shaping, and error prose.

Deliberately free of Hermes imports so every function here is unit-testable on
any machine. The provider module threads these through.
"""

from __future__ import annotations


SYSTEM_PROMPT_BLOCK = """
Long-term memory is provided by OpenLoft Memory. Relevant memories are
automatically recalled before each turn; you also have memory tools:
loft_recall (read what it knows), loft_remember (save a fact), loft_forget
(stop retrieving a fact — stored data is retained; this is not erasure),
loft_stats, loft_search. Facts decay on a curve — fading is a feature, not a
bug.
""".strip()


def shrink(text: str, budget: int) -> str:
    """Trim injected context to a budget, keeping head and tail."""
    if len(text) <= budget:
        return text
    head = int(budget * 0.7)
    tail = budget - head
    return f"{text[:head]}\n…[{len(text) - budget} chars trimmed]…\n{text[-tail:]}"


def turn_payload(user: str, assistant: str) -> list:
    """OpenAI-style minimal transcript for remember, no-op when both empty."""
    payload = [
        {"role": "user", "content": str(user or "")},
        {"role": "assistant", "content": str(assistant or "")},
    ]
    return [m for m in payload if m["content"]]


def friendly_error(exc: Exception) -> str:
    """Model-facing error prose. Short, actionable, no stack traces."""
    text = str(exc) or type(exc).__name__
    if type(exc).__name__ == "NotProvisioned":
        return f"{text} — mint a memory key from the dashboard, or check your key"
    if type(exc).__name__ == "RateLimited":
        return f"{text} — slow down and retry shortly"
    return text


def tool_schemas(agent_suffix: str = "") -> list:
    """Model-facing schemas. Honest verbs: forget INVALIDATES, it does not erase."""
    return [
        {
            "name": "loft_recall",
            "description": f"Retrieve relevant long-term memory for a query. "
                           f"Read-only, no side effects.{agent_suffix}",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up"},
                    "top_k": {"type": "integer", "description": "Max hits (default 8)"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "loft_remember",
            "description": "Persist a fact for later recall across sessions "
                           "(costs one extraction call server-side).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The fact to remember"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "loft_forget",
            "description": "Make a stored fact stop being retrieved "
                           "(temporal invalidation — the data is retained, not deleted).",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string",
                                    "description": "Forget all facts about this entity"},
                    "record_id": {"type": "string", "description": "Forget one record"},
                },
                "required": [],
            },
        },
        {
            "name": "loft_stats",
            "description": f"Memory store status: turn number and record counts.{agent_suffix}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "loft_search",
            "description": "Scored vector search over stored memory records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 10)"},
                },
                "required": ["query"],
            },
        },
    ]
