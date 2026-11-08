"""Pure provider wiring tests — no Hermes and no network."""

import pytest

from openloft_memory.schemas import (
    SYSTEM_PROMPT_BLOCK,
    friendly_error,
    shrink,
    tool_schemas,
    turn_payload,
)


class TestShrink:
    def test_short_text_passes_through_unchanged(self):
        assert shrink("abc", 6000) == "abc"

    def test_exactly_budget_unchanged(self):
        text = "a" * 6000
        assert shrink(text, 6000) == text

    def test_trims_to_budget_with_marker(self):
        text = "a" * 10000
        out = shrink(text, 1000)
        assert len(out) <= 1000 + len("\n…[9000 chars trimmed]…\n") + 8
        assert "9000 chars trimmed" in out

    def test_keeps_head_and_tail(self):
        text = "HEAD" + "x" * 2000 + "TAIL"
        out = shrink(text, 100)
        assert out.startswith("HEAD")
        assert out.endswith("TAIL")


class TestTurnPayload:
    def test_both_parts(self):
        payload = turn_payload("u", "a")
        assert payload == [{"role": "user", "content": "u"},
                           {"role": "assistant", "content": "a"}]

    def test_missing_parts_dropped(self):
        assert turn_payload("", "") == []
        assert turn_payload(None, "only") == [{"role": "assistant", "content": "only"}]


class TestFriendlyError:
    def test_not_provisioned_gets_remedy(self):
        class NotProvisioned(Exception):
            pass
        out = friendly_error(NotProvisioned("tenant not provisioned"))
        assert "mint a memory key" in out

    def test_plain_errors_passthrough(self):
        assert friendly_error(ValueError("boom")) == "boom"

    def test_never_protoleaks_traceback(self):
        assert "Traceback" not in friendly_error(RuntimeError("x"))


class TestToolSchemas:
    def test_five_tools_with_names(self):
        names = [s["name"] for s in tool_schemas()]
        assert names == ["loft_recall", "loft_remember", "loft_forget",
                         "loft_stats", "loft_search"]

    def test_forget_is_not_erasure(self):
        forget = next(s for s in tool_schemas() if s["name"] == "loft_forget")
        assert "temporal invalidation" in forget["description"]
        assert "not deleted" in forget["description"]

    def test_schemas_are_jsonable(self):
        import json
        json.dumps(tool_schemas(), default=str)

    def test_system_prompt_honest_decay_copy(self):
        assert "feature" in SYSTEM_PROMPT_BLOCK
        assert "not erasure" in SYSTEM_PROMPT_BLOCK
