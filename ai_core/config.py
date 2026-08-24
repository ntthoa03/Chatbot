"""Nạp cấu hình hành vi/kỹ thuật của tenant.

Giá và mô tả chi tiết dịch vụ phải đi qua chunk RAG hoặc kết quả tool có nguồn.
Config chỉ giữ mã nhóm được/phải báo giá qua chuyên viên và kênh liên hệ tối thiểu.
"""

from __future__ import annotations

from decimal import Decimal
from copy import deepcopy
from pathlib import Path

import yaml
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"
GUARDRAIL_PROFILES_DIR = Path(__file__).resolve().parent.parent / "guardrail_profiles"
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
    label: str | None = Field(default=None, min_length=1)
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
    label: str | None = Field(default=None, min_length=1)
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
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    refusal_message: str = Field(min_length=1)
    # Tenant nhập danh sách reason ngắn bằng khóa YAML `forbidden`. Field nội bộ
    # dùng tên khác để property `forbidden` bên dưới vẫn trả mô tả dễ đọc cho prompt.
    forbidden_rule_ids: list[str] | None = Field(default=None, validation_alias="forbidden")
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
    # Model nhỏ kiểm tra ngữ nghĩa output chỉ được chặn thêm, không được mở
    # một kết quả mà rule cứng đã chặn. Việc bật/tắt rollout nằm trong .env.
    output_model: str | None = Field(default=None, min_length=1)
    output_model_timeout_seconds: float = Field(default=10.0, ge=10.0, le=30.0)
    output_model_min_confidence: float = Field(default=0.85, ge=0.5, le=1.0)
    # HOA-12 là lớp rủi ro cao: khi đã bật model kiểm duyệt mà model lỗi/timeout,
    # không gửi output chưa được duyệt cho khách.
    output_model_fail_closed: bool = True
    output: OutputGuardrailConfig = Field(default_factory=OutputGuardrailConfig)

    @field_validator("forbidden_rule_ids")
    @classmethod
    def validate_forbidden_rule_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if len(values) != len(set(values)):
            raise ValueError("guardrails.forbidden không được chứa reason trùng nhau")
        for value in values:
            if not TENANT_ID_PATTERN.fullmatch(value):
                raise ValueError(
                    "mỗi guardrails.forbidden phải là reason gồm chữ thường, số, '_' hoặc '-'"
                )
        return values

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
    cached_input_usd_per_million: float | None = Field(default=None, ge=0.0)
    cache_write_input_usd_per_million: float | None = Field(default=None, ge=0.0)
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
    costs: list[ModelCostConfig] = Field(default_factory=list)

    def estimate_cost_usd(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        *,
        cached_tokens_in: int = 0,
        cache_write_tokens_in: int = 0,
    ) -> float:
        """Tính list-price USD từ usage token do provider trả về.

        Dùng Decimal để các lượt rất rẻ không bị làm tròn thành 0. Cached/cache-write
        chỉ được tách riêng khi SDK báo số token tương ứng; nếu bảng giá không khai
        báo mức riêng thì dùng giá input thường để tránh đánh giá thấp chi phí.
        """

        rate = next((item for item in self.costs if item.model == model), None)
        if rate is None:
            return 0.0
        counts = (tokens_in, tokens_out, cached_tokens_in, cache_write_tokens_in)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("Số token tính chi phí phải là số nguyên không âm.")

        cached_tokens = min(cached_tokens_in, tokens_in)
        cache_write_tokens = min(cache_write_tokens_in, tokens_in - cached_tokens)
        uncached_tokens = tokens_in - cached_tokens - cache_write_tokens
        cached_rate = (
            rate.cached_input_usd_per_million
            if rate.cached_input_usd_per_million is not None
            else rate.input_usd_per_million
        )
        cache_write_rate = (
            rate.cache_write_input_usd_per_million
            if rate.cache_write_input_usd_per_million is not None
            else rate.input_usd_per_million
        )
        million = Decimal("1000000")
        usd = (
            Decimal(uncached_tokens) * Decimal(str(rate.input_usd_per_million))
            + Decimal(cached_tokens) * Decimal(str(cached_rate))
            + Decimal(cache_write_tokens) * Decimal(str(cache_write_rate))
            + Decimal(tokens_out) * Decimal(str(rate.output_usd_per_million))
        ) / million
        return float(usd.quantize(Decimal("0.000000000001")))


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
    guardrail_profile: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")

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


def _merge_mapping(base: dict, override: dict) -> dict:
    """Ghép dictionary đệ quy; danh sách thường được tenant/profile sau thay thế."""

    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_mapping(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _merge_output_guardrails(base: dict, override: dict) -> dict:
    """Ghép output guardrail và thay rule trùng `reason` theo lớp sau."""

    merged = _merge_mapping(base, {key: value for key, value in override.items() if key != "rules"})
    rules_by_reason: dict[str, dict] = {}
    reason_order: list[str] = []
    for rule in [*(base.get("rules") or []), *(override.get("rules") or [])]:
        if not isinstance(rule, dict) or not isinstance(rule.get("reason"), str):
            raise ConfigError("Mỗi guardrail profile rule phải là object có reason hợp lệ.")
        reason = rule["reason"]
        # Rule của lớp sau vừa ghi đè nội dung vừa giữ thứ tự của chính lớp đó.
        # Điều này tránh profile `common` vô tình đổi độ ưu tiên rule của ngành.
        if reason in reason_order:
            reason_order.remove(reason)
        reason_order.append(reason)
        rules_by_reason[reason] = deepcopy(rule)
    ordered_rules = [rules_by_reason[reason] for reason in reason_order]
    if ordered_rules or "rules" in base or "rules" in override:
        merged["rules"] = ordered_rules
    return merged


def _load_guardrail_profile(profile_id: str, stack: tuple[str, ...] = ()) -> dict:
    """Nạp profile và các profile cha, có chặn path traversal và vòng kế thừa."""

    profile_id = validate_tenant_id(profile_id)
    if profile_id in stack:
        chain = " -> ".join((*stack, profile_id))
        raise ConfigError(f"Guardrail profile kế thừa vòng: {chain}.")

    path = GUARDRAIL_PROFILES_DIR / f"{profile_id}.yaml"
    if not path.exists():
        raise ConfigError(
            f"Không tìm thấy guardrail_profile '{profile_id}' tại {path}."
        )
    with path.open("r", encoding="utf-8") as file:
        try:
            profile = yaml.safe_load(file)
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"Guardrail profile '{profile_id}' bị lỗi cú pháp: {exc}"
            ) from exc

    if not isinstance(profile, dict):
        raise ConfigError(f"Guardrail profile '{profile_id}' phải là YAML object.")
    unknown_keys = set(profile) - {"profile_id", "extends", "output"}
    if unknown_keys:
        raise ConfigError(
            f"Guardrail profile '{profile_id}' có trường không hỗ trợ: "
            + ", ".join(sorted(unknown_keys))
        )
    if profile.get("profile_id") != profile_id:
        raise ConfigError(
            f"File profile '{profile_id}' phải khai báo profile_id='{profile_id}'."
        )

    parents = profile.get("extends", [])
    if isinstance(parents, str):
        parents = [parents]
    if not isinstance(parents, list) or not all(isinstance(item, str) for item in parents):
        raise ConfigError(f"Guardrail profile '{profile_id}'.extends phải là chuỗi hoặc danh sách chuỗi.")
    output = profile.get("output", {})
    if not isinstance(output, dict):
        raise ConfigError(f"Guardrail profile '{profile_id}'.output phải là YAML object.")

    merged: dict = {}
    for parent in parents:
        merged = _merge_output_guardrails(
            merged,
            _load_guardrail_profile(parent, (*stack, profile_id)),
        )
    return _merge_output_guardrails(merged, output)


def _apply_guardrail_profile(data: dict) -> dict:
    """Ghép profile với override tenant rồi lọc bằng danh sách forbidden ngắn."""

    resolved = deepcopy(data)
    guardrails = resolved.get("guardrails")
    if not isinstance(guardrails, dict):
        return resolved

    profile_id = resolved.get("guardrail_profile")
    tenant_output = guardrails.get("output", {})
    if not isinstance(tenant_output, dict):
        raise ConfigError("guardrails.output phải là YAML object.")
    profile_output = _load_guardrail_profile(profile_id) if profile_id else {}
    merged_output = _merge_output_guardrails(profile_output, tenant_output)

    selected = guardrails.get("forbidden")
    if selected is not None:
        if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
            raise ConfigError("guardrails.forbidden phải là danh sách tên quy tắc.")
        aliases: dict[str, str] = {}

        def register_alias(alias: object, reason: str) -> None:
            if not isinstance(alias, str) or not alias.strip():
                return
            normalized = alias.strip().casefold()
            existing = aliases.get(normalized)
            if existing is not None and existing != reason:
                raise ConfigError(
                    f"Nhãn forbidden '{alias}' bị trùng giữa reason '{existing}' và '{reason}'."
                )
            aliases[normalized] = reason

        # Cho phép người quản trị dùng nhãn tiếng Việt; reason kỹ thuật vẫn được
        # chấp nhận để tương thích với config cũ và giữ log/test ổn định.
        for rule in merged_output.get("rules", []):
            if not isinstance(rule, dict) or not isinstance(rule.get("reason"), str):
                continue
            reason = rule["reason"]
            register_alias(reason, reason)
            register_alias(rule.get("label"), reason)
            register_alias(rule.get("description"), reason)
        grounding = merged_output.get("grounding", {})
        grounding_reason = grounding.get("reason", "ungrounded_claim") if isinstance(grounding, dict) else None
        if grounding_reason:
            register_alias(grounding_reason, grounding_reason)
            register_alias(grounding.get("label"), grounding_reason)
            register_alias(grounding.get("description"), grounding_reason)

        selected_reasons: list[str] = []
        missing: list[str] = []
        for item in selected:
            reason = aliases.get(item.strip().casefold())
            if reason is None:
                missing.append(item)
            elif reason not in selected_reasons:
                selected_reasons.append(reason)
        if missing:
            raise ConfigError(
                "guardrails.forbidden tham chiếu tên không có trong profile/output: "
                + ", ".join(missing)
            )
        selected_set = set(selected_reasons)
        merged_output["rules"] = [
            rule for rule in merged_output.get("rules", []) if rule.get("reason") in selected_set
        ]
        if isinstance(grounding, dict):
            grounding = deepcopy(grounding)
            grounding["enabled"] = grounding_reason in selected_set
            merged_output["grounding"] = grounding
        # Schema/runtime chỉ giữ reason chuẩn; file YAML vẫn dùng nhãn tiếng Việt.
        guardrails["forbidden"] = selected_reasons

    guardrails["output"] = merged_output
    resolved["guardrails"] = guardrails
    return resolved


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

    data = _apply_guardrail_profile(_load_yaml(tenant_id))

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
