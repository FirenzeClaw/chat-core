"""配置管理 — YAML + 环境变量，支持 ${VAR} 替换 + schema 校验 + .env 自动加载"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"
DEFAULT_ENV_PATH = Path(__file__).parent / ".env"


def _load_dotenv(path: Path) -> None:
    """从 .env 文件加载环境变量（不覆盖已有的）"""
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value

# 自动加载 chat_core/.env
_load_dotenv(DEFAULT_ENV_PATH)


class ConfigError(Exception):
    """配置校验失败"""


def _subst_env(value: Any) -> Any:
    """递归替换字符串中的 ${VAR_NAME} 为环境变量值"""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}")
        return pattern.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _subst_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst_env(v) for v in value]
    return value


class Config:
    """配置单例，加载 YAML + 环境变量覆盖 + schema 校验"""

    # ── 必填 key schema ─────────────────────────────────────
    REQUIRED_TOP_KEYS = ["apis", "brains", "systems", "prompts", "safety"]
    REQUIRED_BRAINS = ["logic", "emotion", "sub_session", "action"]
    REQUIRED_API_FIELDS = ["base_url", "api_key"]

    _instance: Config | None = None

    def __init__(self, config_path: Path | None = None):
        self._path = config_path or DEFAULT_CONFIG_PATH
        self._data: dict[str, Any] = {}
        self._warnings: list[str] = []
        self._load()
        self._validate()
        self._print_warnings()

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"Config file not found: {self._path}")
        with open(self._path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        self._data = _subst_env(raw) if raw else {}

    # ── Schema 校验 ──────────────────────────────────────────

    def _validate(self) -> None:
        """校验 config 结构完整性"""
        errors: list[str] = []

        # 1. 必填顶级 key
        for key in self.REQUIRED_TOP_KEYS:
            if key not in self._data:
                errors.append(f"缺少必填配置项: '{key}'")

        # 2. apis 校验
        apis = self._data.get("apis", {})
        if not apis:
            errors.append("apis 配置为空，至少需要配置一个 LLM provider")
        else:
            for api_name, api_cfg in apis.items():
                if not isinstance(api_cfg, dict):
                    errors.append(f"apis.{api_name} 必须是对象")
                    continue
                for field in self.REQUIRED_API_FIELDS:
                    if field not in api_cfg:
                        errors.append(f"apis.{api_name} 缺少必填字段: '{field}'")
                # 检查 API key 是否已解析（${VAR} 替换后非空）
                if api_cfg.get("api_key", "") == "":
                    self._warnings.append(f"apis.{api_name}.api_key 为空或环境变量未设置")

        # 3. brains 校验
        brains = self._data.get("brains", {})
        for brain_name in self.REQUIRED_BRAINS:
            if brain_name not in brains:
                errors.append(f"brains 缺少必填脑配置: '{brain_name}'")
                continue
            bc = brains[brain_name]
            if not isinstance(bc, dict):
                errors.append(f"brains.{brain_name} 必须是对象")
                continue
            for field in ["api", "model"]:
                if field not in bc:
                    errors.append(f"brains.{brain_name} 缺少必填字段: '{field}'")
            # 引用检查：api 指向的 provider 必须存在
            api_ref = bc.get("api", "")
            if api_ref and api_ref not in apis:
                errors.append(f"brains.{brain_name}.api='{api_ref}' 引用了不存在的 provider")

        # 4. 类型校验
        for brain_name in self.REQUIRED_BRAINS:
            bc = brains.get(brain_name, {})
            for num_field in ["temperature", "max_tokens"]:
                val = bc.get(num_field)
                if val is not None and not isinstance(val, (int, float)):
                    errors.append(f"brains.{brain_name}.{num_field} 必须是数字，当前值: {val}")

        if errors:
            raise ConfigError(
                f"配置校验失败 ({len(errors)} 项):\n  " + "\n  ".join(errors)
            )

    def _print_warnings(self) -> None:
        for w in self._warnings:
            print(f"[chat-core] ⚠ {w}", file=sys.stderr)

    # ── 便捷属性 ────────────────────────────────────────────

    @property
    def apis(self) -> dict[str, dict[str, Any]]:
        return self._data.get("apis", {})

    @property
    def brains(self) -> dict[str, dict[str, Any]]:
        return self._data.get("brains", {})

    @property
    def systems(self) -> dict[str, Any]:
        return self._data.get("systems", {})

    @property
    def prompts(self) -> dict[str, str]:
        return self._data.get("prompts", {})

    @property
    def safety(self) -> dict[str, Any]:
        return self._data.get("safety", {})

    @property
    def multimodal(self) -> dict[str, Any]:
        return self._data.get("multimodal", {})

    @property
    def history(self) -> dict[str, Any]:
        return self._data.get("history", {})

    # ── 脑配置快捷方法 ─────────────────────────────────────

    def brain_config(self, brain_name: str) -> dict[str, Any]:
        return self.brains.get(brain_name, {})

    def brain_api_config(self, brain_name: str) -> dict[str, Any]:
        bc = self.brain_config(brain_name)
        api_name = bc.get("api", "deepseek")
        api_cfg = dict(self.apis.get(api_name, {}))
        api_cfg["model"] = bc.get("model", "")
        api_cfg["temperature"] = bc.get("temperature", 0.7)
        api_cfg["max_tokens"] = bc.get("max_tokens", 512)
        api_cfg["max_context_tokens"] = bc.get("max_context_tokens", 32000)
        api_cfg["reasoning_effort"] = bc.get("reasoning_effort", "max")
        return api_cfg

    def brain_max_iter(self, brain_name: str) -> int:
        return self.brain_config(brain_name).get("max_iter", 5)

    def brain_max_concurrent(self, brain_name: str) -> int:
        return self.brain_config(brain_name).get("max_concurrent", 2)

    # ── 系统参数 ────────────────────────────────────────────

    def emotion_config(self) -> dict[str, Any]:
        return self.systems.get("emotion", {})

    def personality_config(self) -> dict[str, Any]:
        return self.systems.get("personality", {})

    def memory_config(self) -> dict[str, Any]:
        return self.systems.get("memory", {})

    def boredom_config(self) -> dict[str, Any]:
        return self.systems.get("boredom", {})

    def interest_config(self) -> dict[str, Any]:
        return self.systems.get("interest", {})

    def attention_config(self) -> dict[str, Any]:
        return self.systems.get("attention", {})

    def qq_config(self) -> dict[str, Any]:
        return self._data.get("qq_bot", {})

    # ── 单例 ────────────────────────────────────────────────

    @classmethod
    def get(cls, config_path: Path | None = None) -> Config:
        if cls._instance is None:
            cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def get_config(config_path: Path | None = None) -> Config:
    return Config.get(config_path)
