# Guardrail profile cho nhiều tenant

## Cách tạo tenant mới

1. Sao chép `tenants/tenant_template.example.yaml` thành `tenants/<tenant_id>.yaml`.
2. Đổi `tenant_id`, persona, model, retrieval và đường dẫn index.
3. Chọn `guardrail_profile`: `common`, `digital_agency` hoặc `medical_clinic`.
4. Trong `guardrails.forbidden`, chỉ liệt kê các nhãn tiếng Việt cần bật.
5. Gọi `load_config("<tenant_id>")` để kiểm tra trước khi chạy UI/index.

Tenant không cần sao chép regex hoặc safe reply. Loader ghép theo thứ tự:

```text
profile cha → profile ngành → guardrails.output override của tenant → lọc forbidden
```

## Profile hiện có

- `common`: bảo vệ OTP/thẻ/mật khẩu và thông tin kỹ thuật bí mật.
- `digital_agency`: guardrail MIMA, gồm cam kết kết quả, hoàn tiền, giảm giá, đối thủ, nội bộ và grounding.
- `medical_clinic`: chẩn đoán, kê thuốc, cấp cứu, cam kết điều trị, dữ liệu bệnh nhân và grounding.

## Override riêng tenant

Chỉ thêm `guardrails.output.rules` khi tenant thật sự cần thay một rule. Rule có cùng `reason` sẽ thay rule trong profile; không cần chép các rule khác.

```yaml
guardrails:
  forbidden: [Cam kết kết quả hoặc thứ hạng]
  refusal_message: "..."
  output:
    rules:
      - reason: result_guarantee
        description: "Quy tắc riêng"
        patterns: ['\\bcam ket\\b']
        request_variants: []
        allow_patterns: []
```

Trong profile, `label` là tên tiếng Việt dành cho người cấu hình; `reason` là mã kỹ thuật ổn định dành cho log và test.

Profile không tồn tại, `forbidden` sai tên, regex lỗi hoặc vòng kế thừa đều làm config lỗi ngay; hệ thống không âm thầm bỏ guardrail.
