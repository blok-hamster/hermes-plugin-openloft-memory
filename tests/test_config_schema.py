"""Config merge semantics — defaults under a flat user JSON."""

import pytest

from openloft_memory.config_schema import (
    DEFAULT_BASE_URL,
    FIELDS,
    RUNTIME_DEFAULTS,
    runtime_config,
)


def test_secret_key_never_appears_in_runtime_defaults():
    assert "api_key" not in RUNTIME_DEFAULTS


def test_schema_fields_have_unique_keys():
    keys = [f["key"] for f in FIELDS]
    assert len(keys) == len(set(keys))
    assert "base_url" in keys and "api_key" in keys


def test_runtime_config_defaults_without_user_file():
    cfg = runtime_config(None)
    assert cfg["base_url"] == DEFAULT_BASE_URL
    assert cfg["conversation_scope"] == "agent"
    assert cfg["inject_budget"] == 6000


def test_runtime_config_user_values_win():
    cfg = runtime_config({"base_url": "http://localhost:8100/v1/memory",
                          "top_k": 12, "unknown_key": 1})
    assert cfg["base_url"] == "http://localhost:8100/v1/memory"
    assert cfg["top_k"] == 12
    # Unknown keys are ignored, not merged into the runtime view.
    assert "unknown_key" not in cfg


def test_runtime_config_carries_hermes_home():
    cfg = runtime_config({}, hermes_home="/home/u/.hermes")
    assert cfg["_hermes_home"] == "/home/u/.hermes"
