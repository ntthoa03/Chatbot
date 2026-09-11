# H4-10 — Câu hỏi gửi tenant hàng tuần

Script: `scripts/generate_weekly_tenant_questions.py`.

Script chỉ lấy 3-5 cụm có tần suất dương từ output H4-02. Câu đại diện bắt buộc phải xuất hiện trong `examples`; nếu tenant có dưới 3 chủ đề, script dừng thay vì tự nghĩ câu hỏi.

```powershell
python scripts/generate_weekly_tenant_questions.py `
  --input outputs/h4_02/knowledge_gap_clusters.json `
  --output-dir outputs/h4_10
```

Output:

- `weekly_questions.json`: câu hỏi, tần suất, cluster và bằng chứng.
- `message_templates.md`: nội dung ngắn để gửi thủ công; script không gửi tin nhắn.

Lần acceptance 5 tenant dùng `tests/fixtures/h4_10_clusters_5_tenants.json` vì output H4-02 hiện tại chưa có gap thật. Fixture và output đều ghi rõ `TEST/SYNTHETIC — không phải log khách thật`; không được gửi các mẫu này cho tenant thật.
