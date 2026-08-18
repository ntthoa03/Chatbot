"""Nạp cấu hình hành vi/kỹ thuật của tenant.

Giá và mô tả chi tiết dịch vụ phải đi qua chunk RAG hoặc kết quả tool có nguồn.
Config chỉ giữ mã nhóm được/phải báo giá qua chuyên viên và kênh liên hệ tối thiểu.
"""

from __future__ import annotations

from pathlib import Path

import yaml
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"
# Chỉ cho phép tenant ID an toàn trước khi dùng giá trị này để tạo đường dẫn file.
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Lỗi khi config thiếu trường bắt buộc hoặc sai định dạng."""


def validate_tenant_id(tenant_id: object) -> str:
    """Validate a tenant identifier before it is used in a filesystem path."""

    # Dùng allow-list thay vì chỉ cấm ".." để chặn cả path traversal và ID nhập sai.
    if not isinstance(tenant_id, str) or not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise ConfigError(
            "tenant_id phải là chuỗi 1-64 ký tự gồm chữ thường, số, '_' hoặc '-', "
            "bắt đầu bằng chữ/số."
        )
    return tenant_id


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


class RequestPolicyVariantConfig(BaseModel):
    """Một biến thể câu hỏi vi phạm và câu trả lời an toàn xác định."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    patterns: list[str] = Field(min_length=1)
    safe_reply: str = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def validate_regexes(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex không hợp lệ '{value}': {exc}") from exc
        return values


class OutputRuleConfig(BaseModel):
    """Một luật chặn đầu ra; pattern chạy trên văn bản viết thường, bỏ dấu."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    description: str = Field(min_length=1)
    enabled: bool = True
    patterns: list[str] = Field(min_length=1)
    allow_patterns: list[str] = Field(default_factory=list)
    request_variants: list[RequestPolicyVariantConfig] = Field(default_factory=list)

    @field_validator("patterns", "allow_patterns")
    @classmethod
    def validate_regexes(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex không hợp lệ '{value}': {exc}") from exc
        return values


class GroundingConfig(BaseModel):
    """Chính sách phát hiện phát biểu thực tế không có bằng chứng."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    reason: str = "ungrounded_claim"
    description: str = "Không bịa thông tin khi không có trong kho tri thức"
    claim_patterns: list[str] = Field(default_factory=list)
    conversation_claim_patterns: list[str] = Field(default_factory=list)
    ignore_patterns: list[str] = Field(default_factory=list)
    min_token_overlap: float = Field(default=0.35, ge=0.0, le=1.0)
    min_matching_tokens: int = Field(default=2, ge=1, le=20)
    request_variants: list[RequestPolicyVariantConfig] = Field(default_factory=list)

    @field_validator("claim_patterns", "conversation_claim_patterns", "ignore_patterns")
    @classmethod
    def validate_regexes(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex không hợp lệ '{value}': {exc}") from exc
        return values


class OutputGuardrailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[OutputRuleConfig] = Field(default_factory=list)
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)
    block_configured_model_names: bool = True

    @model_validator(mode="after")
    def reasons_must_be_unique(self) -> "OutputGuardrailConfig":
        reasons = [rule.reason for rule in self.rules]
        if len(reasons) != len(set(reasons)):
            raise ValueError("guardrails.output.rules.reason phải là duy nhất")
        return self


class GuardrailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refusal_message: str = Field(min_length=1)
    customer_upset_message: str = Field(
        default=(
            "Dạ, em rất tiếc vì trải nghiệm vừa rồi đã khiến anh/chị không hài lòng. "
            "Em ghi nhận phản ánh này và xin phép chuyển ngay anh/chị đến chuyên viên "
            "phụ trách để hỗ trợ xử lý khiếu nại ạ."
        ),
        min_length=1,
    )
    escalate_when: list[str] = Field(default_factory=list)
    seo_phrasing_example: SeoPhrasingExample | None = None
    input_model: str | None = Field(default=None, min_length=1)
    # Gemini API enforces a minimum manually configured deadline of 10 seconds.
    input_model_timeout_seconds: float = Field(default=10.0, ge=10.0, le=30.0)
    output: OutputGuardrailConfig = Field(default_factory=OutputGuardrailConfig)

    @property
    def forbidden(self) -> list[str]:
        """Một nguồn duy nhất cho cả prompt HOA-07 và enforcement HOA-12."""

        descriptions = [rule.description for rule in self.output.rules if rule.enabled]
        if self.output.grounding.enabled:
            descriptions.append(self.output.grounding.description)
        return descriptions


class PricingConfig(BaseModel):
    """Chỉ là mã định tuyến; không chứa giá, note hoặc mô tả dịch vụ."""

    model_config = ConfigDict(extra="forbid")

    can_quote: list[str] = Field(default_factory=list)
    must_contact: list[str] = Field(default_factory=list)


class ContactConfig(BaseModel):
    """Kênh liên hệ tối thiểu bot phải biết theo mẫu tenant."""

    model_config = ConfigDict(extra="forbid")

    hotline: str | None = None
    zalo: str | None = None


class LeadConfig(BaseModel):
    """Tenant-tunable lead capture policy (HOA-14)."""

    model_config = ConfigDict(extra="forbid")

    ask_after_turns: int = Field(default=3, ge=1)
    max_requests: int = Field(default=2, ge=1)
    no_data_retry_message: str = Field(
        default=(
            "Hiện em chưa có đủ dữ liệu để trả lời chính xác. "
            "Anh/chị có thể mô tả rõ hơn nhu cầu để em kiểm tra tiếp không ạ?"
        ),
        min_length=1,
    )


class ModelCostConfig(BaseModel):
    """List-price estimate used for comparable eval cost reporting."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    input_usd_per_million: float = Field(ge=0.0)
    output_usd_per_million: float = Field(ge=0.0)


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
    usd_to_vnd: float = Field(default=26_000.0, gt=0.0)
    costs: list[ModelCostConfig] = Field(default_factory=list)

    def estimate_cost_vnd(self, model: str, tokens_in: int, tokens_out: int) -> float:
        rate = next((item for item in self.costs if item.model == model), None)
        if rate is None:
            return 0.0
        usd = (
            tokens_in * rate.input_usd_per_million
            + tokens_out * rate.output_usd_per_million
        ) / 1_000_000
        return round(usd * self.usd_to_vnd, 4)


class EmbeddingModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(pattern="^(gemini|openai)$")
    model: str = Field(min_length=1)


class EmbeddingPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: EmbeddingModelConfig
    fallback: EmbeddingModelConfig


class RetrievalPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_score: float = Field(ge=0.0, le=1.0)
    relative_score_margin: float = Field(default=0.05, ge=0.0, le=1.0)


class KnowledgeConfig(BaseModel):
    """Tenant-owned local knowledge location.

    Paths are repository-relative so a tenant YAML cannot silently point at an
    arbitrary absolute directory outside the project.
    """

    model_config = ConfigDict(extra="forbid")

    local_index_dir: str = Field(default="index", min_length=1)

    @field_validator("local_index_dir")
    @classmethod
    def validate_local_index_dir(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        path = Path(normalized)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("phải là đường dẫn tương đối trong project và không chứa '..'")
        return normalized


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1)
    config_version: int = 1

    persona: PersonaConfig
    guardrails: GuardrailsConfig
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    contact: ContactConfig = Field(default_factory=ContactConfig)
    lead: LeadConfig = Field(default_factory=LeadConfig)
    enabled_tools: list[str] = Field(default_factory=list)
    model_policy: ModelPolicyConfig
    embedding_policy: EmbeddingPolicyConfig
    retrieval_policy: RetrievalPolicyConfig
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)

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

    # Phải kiểm tra trước khi ghép tenant_id vào đường dẫn tenants/{tenant_id}.yaml.
    tenant_id = validate_tenant_id(tenant_id)
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
