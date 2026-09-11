# H4-06 — Import hỏi đáp từ log chat cũ

## Phạm vi

`ingestion/chat_log_importer.py` nhận CSV, JSON hoặc JSONL theo hai dạng:

- mỗi record là một message với `conversation_id`, `role`, `message`;
- mỗi record là một cặp với `conversation_id`, `question`, `answer`.

Các alias thường gặp như `thread_id`, `sender_type`, `text`, `reply` cũng được
nhận. Mọi record có `tenant_id` khác tenant đang import đều bị từ chối trước
khi xử lý.

## Thứ tự bảo vệ dữ liệu

1. Đọc export trong bộ nhớ và kiểm tra tenant.
2. Che số điện thoại, email và tên khách cuối.
3. Chỉ trên bản đã che: bỏ nhiễu hiển nhiên, ghép lượt khách–nhân viên và gom
   các câu hỏi tương tự.
4. Gửi từng batch đã ẩn danh cho LLM của tenant. LLM lọc cặp có tri thức tái
   sử dụng, viết câu trả lời ngắn gọn nhưng không thêm dữ kiện và giữ nguyên
   cách xưng hô/giọng điệu gốc. Thiếu quyết định, sai schema hoặc lỗi provider
   đều fail-closed, không ghi output một phần.
5. Quét PII lần cuối rồi ghi `qa_pairs.redacted.json`,
   `candidate_chunks.DO_NOT_INDEX.json` và sản phẩm rõ ràng
   `extracted_knowledge.DO_NOT_INDEX.json` vào vùng staging.

Không có tùy chọn tắt che PII. Bộ tri thức trích được tuân thủ nguyên schema
`KnowledgeChunk`, không sửa `ai_core/interfaces.py`, nhưng **chưa được phép
index** trước khi H4-07 chia tách eval/knowledge. Chi tiết quyết định LLM nằm
trong `llm_review` của Q&A staging và số lượt gọi/model/token nằm trong
`audit.json`; chúng không làm thay đổi schema chunk cố định.

## Chạy bộ TEST/SYNTHETIC

```powershell
python -m ingestion.chat_log_importer `
  docs\test_logs_H4-06\sample_chat_export.csv `
  --tenant-id mima_internal `
  --output-dir outputs\h4_06 `
  --minimum-pairs 1
```

Lệnh trên mặc định bắt buộc dùng model chính của tenant và model dự phòng nếu
model chính lỗi. `--skip-llm` chỉ dành cho test parser offline; kết quả đó không
đạt nghiệm thu phần làm giàu bằng LLM.

Số lượng đầu ra phụ thuộc quyết định lọc của LLM và được ghi trong `audit.json`;
không còn coi toàn bộ cặp vượt ngưỡng độ dài là tri thức hợp lệ.

> Đây chỉ là dữ liệu TEST/SYNTHETIC. Phải chạy lại và kiểm tra mẫu thủ công
> khi có export thật đã được tenant cho phép sử dụng.
