"""Kiểu dữ liệu và lỗi dùng chung cho hệ thống tool calling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


class ToolError(RuntimeError):
    """Lỗi nền của hệ thống tool."""


class ToolNotFoundError(ToolError):
    """Tool chưa được đăng ký hoặc không được tenant bật."""


class ToolValidationError(ToolError):
    """Tham số gọi tool không khớp JSON Schema."""


class ToolTimeoutError(ToolError):
    """Tool không hoàn thành trong thời gian cho phép."""


class ToolExecutionError(ToolError):
    """Handler của tool trả lỗi."""


@dataclass(frozen=True)
class ToolDefinition:
    """Định nghĩa tool độc lập với provider LLM.

    ``args_model`` vừa sinh JSON Schema gửi cho model, vừa validate lại tham số
    ở biên tin cậy trước khi gọi handler.
    """

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[..., dict[str, Any]]
    timeout_seconds: float = 2.0
    result_model: type[BaseModel] | None = None
    matches_message: Callable[[str], bool] | None = None

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
        }

    def validate_args(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ToolValidationError(f"Tham số tool '{self.name}' phải là object.")
        try:
            return self.args_model.model_validate(args).model_dump()
        except ValidationError as exc:
            raise ToolValidationError(
                f"Tham số tool '{self.name}' không hợp lệ: {exc.errors(include_url=False)}"
            ) from exc

    def validate_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ToolExecutionError(f"Tool '{self.name}' phải trả về object.")
        if self.result_model is None:
            return result
        try:
            return self.result_model.model_validate(result).model_dump()
        except ValidationError as exc:
            raise ToolExecutionError(
                f"Kết quả tool '{self.name}' không hợp lệ: {exc.errors(include_url=False)}"
            ) from exc

    def may_match(self, message: str) -> bool:
        return bool(self.matches_message and self.matches_message(message))
