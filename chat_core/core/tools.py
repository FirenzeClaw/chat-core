"""ToolRegistry — 工具注册与分发，支持并行/串行混合执行"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from chat_core.core.types import ToolCall, ToolContext, ToolSpec


class ToolDefinition:
    """单个工具定义"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        fn: Callable[..., Any],
        parallel_safe: bool = True,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn
        self.parallel_safe = parallel_safe

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            type="function",
            function={
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        )


class ToolRegistry:
    """工具注册表 — 管理注册、查询、执行"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._plan_mode = False

    @property
    def plan_mode(self) -> bool:
        return self._plan_mode

    def set_plan_mode(self, on: bool) -> None:
        self._plan_mode = on

    def register(self, defn: ToolDefinition) -> None:
        if defn.name in self._tools:
            raise ValueError(f"Tool '{defn.name}' already registered")
        self._tools[defn.name] = defn

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [d.to_spec() for d in self._tools.values()]

    # ── 执行 ────────────────────────────────────────────────

    async def execute(self, tool_call: ToolCall, ctx: ToolContext) -> str:
        tool = self._tools.get(tool_call.function_name)
        if not tool:
            return json.dumps({"error": f"Unknown tool: {tool_call.function_name}"})

        try:
            args = json.loads(tool_call.function_args or "{}")
        except json.JSONDecodeError:
            return json.dumps({"error": f"Invalid JSON arguments for {tool_call.function_name}"})

        try:
            result = tool.fn(args, ctx)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except Exception as e:
            return json.dumps({"error": f"Tool {tool_call.function_name} failed: {e}"})

    async def execute_batch(self, tool_calls: list[ToolCall], ctx: ToolContext) -> list[str]:
        """并行执行 parallel_safe 工具，串行执行其余"""
        results: list[str | None] = [None] * len(tool_calls)
        parallel_tasks: list[tuple[int, ToolCall]] = []
        serial_tasks: list[tuple[int, ToolCall]] = []

        for i, call in enumerate(tool_calls):
            tool = self._tools.get(call.function_name)
            if tool and tool.parallel_safe:
                parallel_tasks.append((i, call))
            else:
                serial_tasks.append((i, call))

        # 并行
        if parallel_tasks:
            parallel_results = await asyncio.gather(
                *[self.execute(call, ctx) for _, call in parallel_tasks],
                return_exceptions=True,
            )
            for (idx, _), result in zip(parallel_tasks, parallel_results):
                results[idx] = str(result) if not isinstance(result, BaseException) else json.dumps({"error": str(result)})

        # 串行
        for idx, call in serial_tasks:
            results[idx] = await self.execute(call, ctx)

        return [r for r in results if r is not None]

    def fork(self) -> ToolRegistry:
        """浅拷贝工具映射（子 registry 可独立增删）"""
        child = ToolRegistry()
        child._tools = dict(self._tools)
        child._plan_mode = self._plan_mode
        return child

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
