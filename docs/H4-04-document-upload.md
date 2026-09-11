# H4-04 — Pipeline nhận tài liệu upload

Pipeline hỗ trợ `PDF`, `DOCX`, `XLSX`, `CSV` và ảnh (`PNG/JPG/WEBP/TIFF/BMP`).
PDF có text layer được đọc trực tiếp; trang PDF scan và ảnh được OCR bằng model
vision theo cấu hình tenant: Gemini chính, OpenAI dự phòng. Không cần cài
Tesseract hoặc OCR binary trên máy chạy.

## Chạy

```powershell
python -m ingestion.document_loader tai-lieu.pdf bang-gia.xlsx `
  --tenant-id mima_internal `
  --output outputs/h4_04/mima-upload-chunks.json
```

File Excel/CSV và bảng trong Word được chuyển thành bảng Markdown, giữ nguyên
hàng/cột và lặp lại header nếu bảng phải chia thành nhiều chunk. Kết quả đầu ra
tuân theo `KnowledgeChunk` hiện tại nên có thể đưa thẳng vào `index_chunks.py`.

## Kiểm duyệt OCR bắt buộc

Nếu có trang scan hoặc ảnh, lần chạy đầu chỉ tạo file `*.ocr-review.json` và
manifest có `ready_for_index=false`; pipeline không tạo file đầu ra sẵn sàng
đánh index. Mở file review, sửa trực tiếp trường `content` của chunk theo file
gốc, rồi promote chính bản đã duyệt:

```powershell
python -m ingestion.ocr_review `
  outputs/h4_04/mima-upload-chunks.ocr-review.json `
  --output outputs/h4_04/mima-upload-chunks.json `
  --reviewer "nghoa" `
  --confirm-reviewed
```

Lệnh promote không mở lại ảnh và không gọi OCR API. Nó chặn chunk còn
`[KHÔNG ĐỌC ĐƯỢC]`, validate lại schema, ghi atomically file chính và bổ sung
người duyệt, thời gian, hash file review/output vào manifest. Manifest OCR lưu
provider/model, thời gian, confidence chất lượng và chi phí. Nếu SDK trả usage,
chi phí dùng token thật nhân đơn giá cấu hình; nếu adapter không trả usage thì
manifest ghi rõ `usage_source=local_estimate`. Confidence luôn ghi rõ đây là
ước lượng chất lượng local, không giả là confidence theo từng ký tự của model.

Máy chạy OCR lần đầu cần có `GEMINI_API_KEY` hoặc `OPENAI_API_KEY`.
`--ocr-reviewed` ở CLI đã bị chặn để tránh vô tình trả phí OCR lần thứ hai.

## Preflight H4-05 trước API trả phí

```powershell
# Chỉ kiểm tra local, không build embedding và không gọi chat/judge
python -m eval.run_h4_05 --tenant-id mima_internal --preflight-only

# Chạy thật sau khi đọc outputs/h4_05/experiment/preflight.md
python -m eval.run_h4_05 `
  --tenant-id mima_internal `
  --data-label REAL_2026_09 `
  --cases eval/cases-thuc-te.yaml `
  --document-chunks outputs/data-that/document-chunks.json `
  --document-manifest outputs/data-that/manifest-rieng.json `
  --expected-evidence eval/expected-evidence-thuc-te.yaml `
  --confirm-paid-run --max-estimated-usd 1.00
```

Preflight kiểm tra case, tenant, chunk, trạng thái OCR review, index/dimension,
credential và quyền ghi output. Không có `--confirm-paid-run`, hoặc chi phí model
ước tính vượt ngân sách, chương trình dừng trước lời gọi API.

`--expected-evidence` là tùy chọn; file có dạng `case_id: "đoạn phải có trong
chunk"`. Không truyền thì audit chỉ kiểm tra chunk thuộc đúng index, không ép
bất kỳ ID case hay nội dung nghiệp vụ nào. `--tenant-id` bắt buộc để tránh chạy
nhầm tenant mặc định.

## Tự kiểm tra và in nội dung

Bộ unit test tự sinh thêm các ca Excel khó (tiêu đề trước header, nhiều bảng,
ghi chú ở cột rời, định dạng tiền, nhiều sheet), CSV có ô nhiều dòng và ký tự
`|`. Chạy:

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m unittest tests.test_h4_04 tests.test_vision_ocr -v
```

Chạy nghiệm thu trên 6 tài liệu mẫu và tạo báo cáo chứa toàn bộ nội dung đã
trích để nhìn trực tiếp:

```powershell
$env:PYTHONIOENCODING='utf-8'
python scripts/run_h4_04_acceptance.py
Get-Content outputs/h4_04/acceptance-report.md -Encoding utf8
```

Lượt nghiệm thu này dùng OCR giả lập cố định để không gửi tài liệu ra ngoài và
có thể chạy lại miễn phí. Nó không thay thế smoke test API vision thật.

## Nạp nhanh trong UI test

Khởi động lại UI bằng `./run_ui.ps1`, chọn **Khu vực → Nạp tài liệu test**:

1. Chọn một hoặc nhiều file và bấm **Trích xuất và xem trước**.
2. Đọc từng chunk. Nếu có OCR, xác nhận đã đối chiếu với file gốc.
3. Bấm **Xác nhận và nạp vào kho TEST**, rồi quay lại **Chatbot** để hỏi.

Mỗi phiên tạo một index riêng dưới `outputs/h4_04/ui_test_indexes/`; `index/`
không bị sửa. Thanh bên hiện **Đang dùng kho tài liệu TEST** và có nút quay lại
kho chính. Chuyển index dùng `ContextVar`, không dùng biến môi trường toàn cục,
nên các phiên test không đổi kho tri thức của nhau.
