"""Registry, kiểm soát quyền tenant và thực thi tool an toàn."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Lock
from typing import Any

from ai_core.tools.base import (
    ToolDefinition,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = Lock()

    def register(self, definition: ToolDefinition, *, replace: bool = False) -> None:
        with self._lock:
            if definition.name in self._tools and not replace:
                raise ValueError(f"Tool '{definition.name}' đã được đăng ký.")
            self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool '{name}' chưa được đăng ký.") from exc

    def enabled(self, names: list[str]) -> dict[str, ToolDefinition]:
        enabled: dict[str, ToolDefinition] = {}
        for name in dict.fromkeys(names):
            enabled[name] = self.get(name)
        return enabled

    def schemas(self, names: list[str]) -> list[dict[str, Any]]:
        return [tool.json_schema() for tool in self.enabled(names).values()]

    def may_handle(self, message: str, names: list[str]) -> bool:
        return any(tool.may_match(message) for tool in self.enabled(names).values())

    def execute(self, name: str, args: dict[str, Any], enabled_names: list[str]) -> dict[str, Any]:
        if name not in enabled_names:
            raise ToolNotFoundError(f"Tenant chưa bật tool '{name}'.")
        definition = self.get(name)
        validated = definition.validate_args(args)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{name}")
        future = executor.submit(definition.handler, **validated)
        try:
            result = future.result(timeout=definition.timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise ToolTimeoutError(
                f"Tool '{name}' quá thời gian {definition.timeout_seconds:g} giây."
            ) from exc
        except Exception as exc:
            raise ToolExecutionError(f"Tool '{name}' thực thi thất bại: {exc}") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return definition.validate_result(result)


TOOL_REGISTRY = ToolRegistry()
