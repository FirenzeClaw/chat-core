"""Config validation tests."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from io import StringIO

import pytest

from chat_core.config import Config, ConfigError, DEFAULT_CONFIG_PATH


# ── Helpers ──────────────────────────────────────────────────

def write_temp_yaml(content: str) -> Path:
    """Write content to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


MINIMAL_VALID_CONFIG = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: test-key-123
brains:
  logic:
    api: test_provider
    model: test-model
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""


class TestConfig:
    """Config loading and validation tests."""

    def setup_method(self):
        """Reset singleton before each test."""
        Config.reset()

    def teardown_method(self):
        """Clean up singleton after each test."""
        Config.reset()

    def test_load_valid_config(self):
        path = write_temp_yaml(MINIMAL_VALID_CONFIG)
        try:
            cfg = Config(path)
            assert cfg.brains is not None
            assert "logic" in cfg.brains
            assert cfg.brains["logic"]["api"] == "test_provider"
            assert cfg.brains["logic"]["model"] == "test-model"
        finally:
            path.unlink(missing_ok=True)

    def test_missing_api_key_warning(self):
        config_no_key = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: ""
brains:
  logic:
    api: test_provider
    model: test-model
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""
        path = write_temp_yaml(config_no_key)
        try:
            old_stderr = sys.stderr
            sys.stderr = StringIO()
            try:
                cfg = Config(path)
                output = sys.stderr.getvalue()
                assert "api_key 为空" in output
            finally:
                sys.stderr = old_stderr
        finally:
            path.unlink(missing_ok=True)

    def test_missing_required_brain(self):
        config_no_logic = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: test-key
brains:
  # 缺少 logic
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""
        path = write_temp_yaml(config_no_logic)
        try:
            with pytest.raises(ConfigError, match="logic"):
                Config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_temperature_type(self):
        config_bad_temp = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: test-key
brains:
  logic:
    api: test_provider
    model: test-model
    temperature: "hot"
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""
        path = write_temp_yaml(config_bad_temp)
        try:
            with pytest.raises(ConfigError, match="temperature"):
                Config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_env_var_substitution(self):
        config_with_env = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: ${TEST_BASE_URL}
    api_key: ${TEST_API_KEY}
brains:
  logic:
    api: test_provider
    model: test-model
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""
        path = write_temp_yaml(config_with_env)
        try:
            os.environ["TEST_BASE_URL"] = "https://env-test.example.com/v1"
            os.environ["TEST_API_KEY"] = "env-key-abc"
            cfg = Config(path)
            assert cfg.apis["test_provider"]["base_url"] == "https://env-test.example.com/v1"
            assert cfg.apis["test_provider"]["api_key"] == "env-key-abc"
        finally:
            path.unlink(missing_ok=True)
            os.environ.pop("TEST_BASE_URL", None)
            os.environ.pop("TEST_API_KEY", None)

    def test_brain_api_reference(self):
        config_bad_ref = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: test-key
brains:
  logic:
    api: nonexistent_provider
    model: test-model
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
safety:
  send_reply_max_length: 500
"""
        path = write_temp_yaml(config_bad_ref)
        try:
            with pytest.raises(ConfigError, match="nonexistent_provider"):
                Config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_missing_top_key(self):
        config_missing_safety = """
version: 1
apis:
  test_provider:
    provider: test
    base_url: https://test.example.com/v1
    api_key: test-key
brains:
  logic:
    api: test_provider
    model: test-model
  emotion:
    api: test_provider
    model: test-model
  sub_session:
    api: test_provider
    model: test-model
  action:
    api: test_provider
    model: test-model
systems:
  emotion:
    tick_interval: 10
prompts:
  persona: ./prompts/persona.yaml
# missing 'safety'
"""
        path = write_temp_yaml(config_missing_safety)
        try:
            with pytest.raises(ConfigError, match="safety"):
                Config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_load_bundled_config(self):
        """Verify bundled config.yaml can be loaded."""
        cfg = Config(DEFAULT_CONFIG_PATH)
        assert "deepseek" in cfg.apis
        assert "logic" in cfg.brains
        assert "emotion" in cfg.brains
        assert "sub_session" in cfg.brains
        assert "action" in cfg.brains
