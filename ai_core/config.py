"""
Nạp config của tenant từ file YAML (persona, guardrails, pricing, contact,
enabled_tools, model_policy).

QUY TẮC BẮT BUỘC (theo task HOA-04 và NT-4 spec kiến trúc multi-tenant):
mọi thứ riêng của một khách hàng (tên bot, hotline, bảng giá được phép nói...)
nằm trong file YAML dưới thư mục tenants/, TUYỆT ĐỐI không hardcode trong file
Python này hay bất cứ đâu trong ai_core/*.

Sửa file YAML là bot đổi hành vi ngay — không cần sửa một dòng code nào.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"


class ConfigError(ValueError):
    """Lỗi khi config thiếu trường bắt buộc hoặc sai định dạng."""


# ============================================================
# Pydantic schemas
# ============================================================


class PersonaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_name: str = Field(min_length=1)
    self_address: str = Field(min_length=1)
    user_address: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    reply_length: str = Field(min_length=1)
    always_end_with_cta: bool = True


class SeoPhrasingExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correct: str = Field(min_length=1)
    incorrect: str = Field(min_length=1)


class GuardrailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden: list[str]
    refusal_message: str = Field(min_length=1)
    escalate_when: list[str] = Field(default_factory=list)
    seo_phrasing_example: SeoPhrasingExample


class PricingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    note: str | None = None


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_quote: list[PricingItem] = Field(default_factory=list)
    must_contact: list[PricingItem] = Field(default_factory=list)


class ContactConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hotline: str | None = None
    zalo: str | None = None


class ModelPolicyConfig(BaseModel):
    """
    Cấu hình model dùng cho tenant.

    primary:
        Model chính.

    fallback:
        Model dự phòng khi model chính không khả dụng hoặc gặp lỗi.

    temperature:
        Tham số temperature khi gọi model.
    """

    model_config = ConfigDict(extra="forbid")

    primary: str = Field(min_length=1)
    fallback: str = Field(min_length=1)
    temperature: float = 0.3


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    config_version: int = 1

    persona: PersonaConfig
    guardrails: GuardrailsConfig
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    contact: ContactConfig = Field(default_factory=ContactConfig)
    enabled_tools: list[str] = Field(default_factory=list)
    model_policy: ModelPolicyConfig

    # --------------------------------------------------------
    # Convenience properties
    # --------------------------------------------------------

    @property
    def bot_name(self) -> str:
        return self.persona.bot_name

    @property
    def refusal_message(self) -> str:
        return self.guardrails.refusal_message

    @property
    def model_primary(self) -> str:
        return self.model_policy.primary

    @property
    def model_fallback(self) -> str:
        return self.model_policy.fallback


# ============================================================
# YAML loader
# ============================================================


def _load_yaml(tenant_id: str) -> dict:
    """Đọc file YAML của tenant."""

    path = TENANTS_DIR / f"{tenant_id}.yaml"

    if not path.exists():
        raise ConfigError(
            f"Không tìm thấy file config cho tenant "
            f"'{tenant_id}' tại {path}."
        )

    with path.open("r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(
                f"File YAML của tenant '{tenant_id}' "
                f"bị lỗi cú pháp: {e}"
            ) from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"File config của tenant '{tenant_id}' "
            "không đúng định dạng YAML object."
        )

    return data


def load_config(
    tenant_id: str,
    config_version: int | None = None,
) -> AgentConfig:
    """
    Load và validate config của tenant.

    Args:
        tenant_id:
            ID của tenant, tương ứng với file:
            tenants/{tenant_id}.yaml

        config_version:
            Nếu truyền vào, kiểm tra version yêu cầu
            có khớp với version trong YAML hay không.

    Returns:
        AgentConfig đã được Pydantic validate.

    Raises:
        ConfigError:
            Khi file không tồn tại, YAML lỗi cú pháp,
            config sai schema hoặc version không khớp.
    """

    data = _load_yaml(tenant_id)

    # --------------------------------------------------------
    # Pydantic validation
    # --------------------------------------------------------

    try:
        config = AgentConfig.model_validate(data)

    except ValidationError as e:
        errors = []

        for error in e.errors():
            location = ".".join(str(x) for x in error["loc"])

            if error["type"] == "missing":
                errors.append(
                    f"Config thiếu trường bắt buộc: '{location}'"
                )

            elif error["type"] == "extra_forbidden":
                errors.append(
                    f"Config chứa trường không được phép: '{location}'"
                )

            else:
                errors.append(
                    f"Config trường '{location}' không hợp lệ: "
                    f"{error['msg']}"
                )

        raise ConfigError(
            f"Config tenant '{tenant_id}' không hợp lệ:\n"
            + "\n".join(f"- {x}" for x in errors)
        ) from e

    # --------------------------------------------------------
    # Validate tenant_id
    # --------------------------------------------------------

    if config.tenant_id != tenant_id:
        raise ConfigError(
            f"File tenants/{tenant_id}.yaml khai báo "
            f"tenant_id='{config.tenant_id}', "
            f"không khớp tenant_id='{tenant_id}'."
        )

    # --------------------------------------------------------
    # Validate config_version
    # --------------------------------------------------------

    if (
        config_version is not None
        and config_version != config.config_version
    ):
        raise ConfigError(
            f"Yêu cầu config_version={config_version} nhưng file hiện có "
            f"config_version={config.config_version}."
        )

    return config