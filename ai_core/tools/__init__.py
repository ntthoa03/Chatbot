"""Public API của khung tool calling HOA-10."""

from __future__ import annotations

from ai_core.tools.base import (
    ToolDefinition,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolValidationError,
)
from ai_core.tools.check_domain import CHECK_DOMAIN_TOOL
from ai_core.tools.registry import TOOL_REGISTRY, ToolRegistry


TOOL_REGISTRY.register(CHECK_DOMAIN_TOOL)


def get_enabled_tools(enabled_tool_names: list[str]) -> dict[str, ToolDefinition]:
    return TOOL_REGISTRY.enabled(enabled_tool_names)


def get_tool_schemas(enabled_tool_names: list[str]) -> list[dict]:
    return TOOL_REGISTRY.schemas(enabled_tool_names)


def message_may_need_tools(message: str, enabled_tool_names: list[str]) -> bool:
    return TOOL_REGISTRY.may_handle(message, enabled_tool_names)


def execute_tool(name: str, args: dict, enabled_tool_names: list[str]) -> dict:
    return TOOL_REGISTRY.execute(name, args, enabled_tool_names)


__all__ = [
    "TOOL_REGISTRY",
    "ToolDefinition",
    "ToolError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolTimeoutError",
    "ToolValidationError",
    "execute_tool",
    "get_enabled_tools",
    "get_tool_schemas",
    "message_may_need_tools",
]
