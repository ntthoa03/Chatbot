"""
Khung tool calling: đăng ký tool theo enabled_tools trong config.
STUB — registry thật thuộc phạm vi task khác.
"""

from __future__ import annotations

TOOL_REGISTRY: dict[str, "callable"] = {}


def get_enabled_tools(enabled_tool_names: list[str]) -> dict:
    return {name: TOOL_REGISTRY[name] for name in enabled_tool_names if name in TOOL_REGISTRY}
