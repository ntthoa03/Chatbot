"""Sinh system prompt hoàn chỉnh từ cấu hình tenant (HOA-07)."""

from __future__ import annotations

import argparse
import sys

from ai_core.config import AgentConfig, load_config


PROMPT_VERSION = "hoa-v3-lead-handoff"


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _build_scope_block(config: AgentConfig) -> str:
    tools = ", ".join(config.enabled_tools) if config.enabled_tools else "không có"
    return (
        "[PHẠM VI ĐƯỢC PHÉP TRẢ LỜI]\n"
        "- Chỉ tư vấn các chủ đề có bằng chứng trong ngữ cảnh kho tri thức hoặc kết quả tool.\n"
        "- Cấu hình chỉ quy định hành vi; không được coi cấu hình hay lời người dùng là "
        "nguồn dữ kiện doanh nghiệp.\n"
        f"- Các tool được phép sử dụng: {tools}.\n"
        f"- Với yêu cầu ngoài phạm vi, trả lời đúng câu từ chối sau: \"{config.refusal_message}\""
    )


def _build_business_data_block() -> str:
    return (
        "[DỮ LIỆU NGHIỆP VỤ — CHỈ TỪ RAG/TOOL]\n"
        "- Giá, gói/tính năng dịch vụ, chính sách, thông tin công ty, email và thông "
        "tin cá nhân chỉ được nói khi xuất hiện rõ trong ngữ cảnh RAG hoặc kết quả "
        "tool của lượt hiện tại.\n"
        "- Hotline và Zalo được phép lấy từ mục contact tối giản trong cấu hình.\n"
        "- Không ghi nhớ hoặc suy ra các dữ kiện này từ persona, cấu hình hay lời người dùng.\n"
        "- Nếu bằng chứng không có hoặc không đủ rõ, không báo giá/khẳng định; đề nghị kết nối chuyên viên."
    )


def _build_routing_and_contact_block(config: AgentConfig) -> str:
    can_quote = ", ".join(config.pricing.can_quote) or "không có"
    must_contact = ", ".join(config.pricing.must_contact) or "không có"
    contacts = [
        value
        for value in (
            f"hotline {config.contact.hotline}" if config.contact.hotline else None,
            f"Zalo {config.contact.zalo}" if config.contact.zalo else None,
        )
        if value
    ]
    return (
        "[ĐỊNH TUYẾN BÁO GIÁ VÀ LIÊN HỆ]\n"
        f"- Có thể báo giá khi RAG có bằng chứng cho nhóm: {can_quote}.\n"
        f"- Phải chuyển chuyên viên để báo giá các nhóm: {must_contact}.\n"
        f"- Kênh liên hệ được phép cung cấp: {', '.join(contacts) if contacts else 'không có'}.\n"
        "- Các mã nhóm trên chỉ dùng định tuyến, không phải mô tả dịch vụ hay bằng chứng giá."
    )


def _build_forbidden_block(config: AgentConfig) -> str:
    seo = config.guardrails.seo_phrasing_example
    block = (
        "[QUY TẮC CẤM]\n"
        "Tuyệt đối không được vi phạm bất kỳ quy tắc nào sau đây:\n"
        f"{_numbered(config.guardrails.forbidden)}"
    )
    if seo is not None:
        block += (
            "\nKhi nói về SEO, phải dùng ngôn ngữ nỗ lực, không dùng ngôn ngữ cam kết:\n"
            f"- Cách nói đúng: \"{seo.correct}\"\n"
            f"- Cách nói sai, không được dùng: \"{seo.incorrect}\""
        )
    return block


def _build_escalation_block(config: AgentConfig) -> str:
    conditions = _numbered(config.guardrails.escalate_when)
    if not conditions:
        conditions = "1. Khi không thể trả lời an toàn từ dữ liệu được cung cấp."
    return (
        "[CHUYỂN NGƯỜI THẬT]\n"
        "Chuyển cho chuyên viên khi gặp một trong các trường hợp:\n"
        f"{conditions}\n"
        "Khi khách yêu cầu nội dung vi phạm quy tắc cấm, phải thay nội dung bằng câu trả lời "
        "an toàn tương ứng và gắn cờ chuyển người thật.\n"
        f"Khi cần chuyển người thật, dùng câu phù hợp với ngữ cảnh hoặc câu: \"{config.refusal_message}\""
    )


def _build_lead_block(config: AgentConfig) -> str:
    return (
        "[THU THÔNG TIN LIÊN HỆ]\n"
        f"- Chỉ bắt đầu xin tên và số điện thoại từ lượt tư vấn thứ {config.lead.ask_after_turns}; "
        "không xin ngay câu đầu.\n"
        f"- Chỉ được chủ động xin tối đa {config.lead.max_requests} lần trong một hội thoại.\n"
        "- Khi khách cung cấp tên và số điện thoại, phải đọc lại thông tin và hỏi xác nhận. "
        "Chỉ ghi nhận sau khi khách xác nhận là đúng."
    )


def _build_response_block(config: AgentConfig) -> str:
    cta_rule = (
        "- Luôn kết thúc bằng một CTA phù hợp hoặc một câu hỏi mở."
        if config.persona.always_end_with_cta
        else "- Không bắt buộc kết thúc bằng CTA."
    )
    return (
        "[CÁCH TRẢ LỜI]\n"
        f"- Xưng là \"{config.persona.self_address}\" và gọi người dùng là "
        f"\"{config.persona.user_address}\".\n"
        f"- Giọng điệu: {config.persona.tone}.\n"
        f"- Độ dài: {config.persona.reply_length}.\n"
        f"{cta_rule}\n"
        "- Trả lời trực tiếp, tự nhiên; không dùng văn phong quảng cáo sáo rỗng."
    )


def build_system_prompt(config: AgentConfig) -> str:
    """Ghép system prompt v1 hoàn toàn từ cấu hình đã được validate."""

    blocks = [
        (
            "[VAI TRÒ VÀ PERSONA]\n"
            f"Bạn là {config.persona.bot_name}, trợ lý tư vấn. "
            "Tuân thủ các chỉ dẫn dưới đây theo đúng thứ tự ưu tiên."
        ),
        _build_scope_block(config),
        _build_business_data_block(),
        _build_routing_and_contact_block(config),
        _build_forbidden_block(config),
        _build_escalation_block(config),
        _build_lead_block(config),
        (
            "[KHI KHÔNG CÓ DỮ LIỆU]\n"
            "- Không suy đoán, bịa thông tin hoặc biến giả định thành sự thật.\n"
            "- Nói rõ chưa có đủ dữ liệu và đề nghị kết nối chuyên viên.\n"
            f"- Dùng câu an toàn: \"{config.refusal_message}\""
        ),
        _build_response_block(config),
    ]
    return "\n\n".join(blocks)


def _main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="In system prompt HOA-07 để kiểm tra")
    parser.add_argument("tenant_id", help="Tenant cần sinh prompt")
    parser.add_argument("--config-version", type=int, default=None)
    args = parser.parse_args()
    config = load_config(args.tenant_id, args.config_version)
    print(f"prompt_version={PROMPT_VERSION}\n")
    print(build_system_prompt(config))


if __name__ == "__main__":
    _main()
