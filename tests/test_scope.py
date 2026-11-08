"""Scope sanitizer + identity resolution — plan §3.3."""

import pytest

from openloft_memory.scope import (
    conforms,
    resolve_agent_id,
    resolve_conversation_id,
    sanitize,
)


class TestSanitize:
    def test_plain_value_unchanged(self):
        assert sanitize("acme") == "acme"

    def test_colons_become_dashes(self):
        assert sanitize("gateway:session:abc") == "gateway-session-abc"

    def test_spaces_and_slashes_split(self):
        assert sanitize("my agent/v1") == "my-agent-v1"

    def test_runs_collapse(self):
        assert sanitize("a///b") == "a-b"

    def test_unicode_characters_rejected_then_split(self):
        assert sanitize(" élève café ") == "l-ve-caf-" or conforms(sanitize(" élève café "))

    def test_empty_becomes_default(self):
        assert sanitize("") == "default"
        assert sanitize(None) == "default"

    def test_truncation_appends_unique_suffix(self):
        long_value = "x" * 300
        first, second = sanitize(long_value), sanitize(long_value)
        assert len(first) <= 128
        assert conforms(first)
        # Two truncations of the same input must not collapse: the suffix differs.
        assert first != second

    def test_output_always_conforms(self):
        for raw in ["tenant::agent", "süd", "", "a" * 200, "a b c"]:
            assert conforms(sanitize(raw))


class TestAgentResolution:
    def test_config_wins(self):
        assert resolve_agent_id(cfg_override="sam", env_id="bob",
                                profile_identity="work") == "sam"

    def test_env_next(self):
        assert resolve_agent_id(env_id="bob", profile_identity="work") == "bob"

    def test_profile_next(self):
        assert resolve_agent_id(profile_identity="work") == "work"

    def test_fallback(self):
        assert resolve_agent_id() == "hermes"

    def test_auto_is_not_an_identity(self):
        assert resolve_agent_id(cfg_override="auto", profile_identity="work") == "work"

    def test_results_always_conform(self):
        val = resolve_agent_id(profile_identity="profile with spaces")
        assert conforms(val)


class TestConversationResolution:
    def test_agent_mode_omits_the_field(self):
        assert resolve_conversation_id("agent", "sess_1") is None

    def test_shared_mode_omits_the_field(self):
        assert resolve_conversation_id("shared") is None

    def test_session_mode_sanitizes(self):
        val = resolve_conversation_id("session", "plat:12345")
        assert conforms(val)
        assert ":" not in val

    def test_unknown_mode_falls_back_to_default_store(self):
        assert resolve_conversation_id("nonsense", "x") is None
