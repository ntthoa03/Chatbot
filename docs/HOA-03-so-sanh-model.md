# H-03 — So sánh model LLM + embedding cho tiếng Việt

|            |                                                                                                                                                         |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Trạng thái | Nghiên cứu + khuyến nghị dựa trên giá/free-tier thật (tra cứu 10/08/2026). **CHƯA có số liệu đo thực tế** (độ trễ, chất lượng câu trả lời) — xem mục 5. |
| Phạm vi    | 2 nhà cung cấp LLM (OpenAI, Google Gemini) + 2 model embedding                                                                                          |

## 0. Điều quan trọng cần đọc trước

Task yêu cầu _"Có bảng số liệu thực đo, không phải cảm nhận"_

## 1. Giá & free-tier thật (tra cứu 10/08/2026)

### LLM (chat)

| Nhà cung cấp  | Model                   | Giá (input/output mỗi 1M token)                | Free tier?                                                                                                |
| ------------- | ----------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Google Gemini | `gemini-2.5-flash`      | $0.30 / $2.50                                  | **Có** — miễn phí qua Google AI Studio, giới hạn theo request/phút, không cần thẻ tín dụng                |
| Google Gemini | `gemini-2.5-flash-lite` | $0.10 / $0.40 (rẻ nhất dòng Gemini còn hỗ trợ) | **Có** — cùng điều kiện free tier                                                                         |
| OpenAI        | `gpt-4o-mini`           | $0.15 / $0.60                                  | **Không còn free tier thường trực** — tài khoản mới chỉ được tặng $5 credit dùng thử, hết hạn sau 3 tháng |
| OpenAI        | `gpt-5.4-mini`          | $0.75 / $4.50                                  | Như trên                                                                                                  |

Lưu ý quan trọng: **Google Gemini Pro (2.5 Pro trở lên) đã bị loại khỏi free
tier từ 01/04/2026** — free tier hiện chỉ còn áp dụng cho dòng Flash/Flash-Lite.
Với 15 câu hỏi test, dòng Flash là lựa chọn đúng để tận dụng free tier.

### Embedding

| Nhà cung cấp  | Model                    | Giá (mỗi 1M token, chỉ tính input) | Free tier?                                                                                            |
| ------------- | ------------------------ | ---------------------------------- | ----------------------------------------------------------------------------------------------------- |
| OpenAI        | `text-embedding-3-small` | $0.02                              | Không có free tier riêng, nhưng $5 credit dùng thử của tài khoản mới thừa sức dùng (≈250 triệu token) |
| Google Gemini | `gemini-embedding-001`   | $0.15                              | **Có** — free tier qua AI Studio, giới hạn request/phút, đủ cho test 15 câu                           |

## 2. Vì sao chọn 2 cặp này để test (chưa phải quyết định cuối)

| Vai trò                | Lựa chọn                          | Lý do chọn để TEST (không phải kết luận cuối)                                                            |
| ---------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------- |
| LLM — ứng viên A       | `gemini-2.5-flash` (Google)       | Có free tier thật (không cần thẻ), đủ mạnh cho hội thoại tư vấn, giá vẫn rẻ nếu sau này phải trả phí     |
| LLM — ứng viên B       | `gpt-4o-mini` (OpenAI)            | Rẻ, phổ biến, tài liệu nhiều, dùng $5 credit dùng thử là đủ cho 15 câu test (chi phí thực tế chỉ vài xu) |
| Embedding — ứng viên A | `text-embedding-3-small` (OpenAI) | Rẻ nhất thị trường ($0.02/1M), tài liệu tốt, hỗ trợ đa ngôn ngữ tốt gồm tiếng Việt                       |
| Embedding — ứng viên B | `gemini-embedding-001` (Google)   | Có free tier thật để test miễn phí, cùng hệ sinh thái với Gemini LLM nếu chọn Gemini làm chính           |

**Đây KHÔNG phải là "chọn vì quen tay"** — cả 2 LLM và cả 2 embedding đều nằm
trong nhóm rẻ nhất/có free tier tại thời điểm tra cứu, đúng tinh thần "Đừng
chọn model chỉ vì quen tay" của task. Việc chốt model chính/dự phòng THẬT SỰ
vẫn phải dựa trên số liệu đo ở mục 5, không phải bảng giá này.

## 3. Bộ 15 câu hỏi test

Xem `tests/hoa03_test_questions.json` — 15 câu hỏi tiếng Việt tự soạn tạm bám
theo `seed_chunks.json` (thiết kế web, SEO, quảng cáo, tên miền, bảo mật...),
**mỗi câu có 2 bản: có dấu và không dấu** (đúng lưu ý của task: "khách hay gõ
không dấu"). Sẽ thay bằng câu hỏi thật khi có bộ câu hỏi thật.

## 4. Cách chạy benchmark thật

```bash
pip install openai google-genai
export OPENAI_API_KEY=sk-...
export GEMINI_API_KEY=...

python tests/hoa03_benchmark.py
```

Script sẽ: gọi cả 2 LLM và cả 2 embedding model trên 15 câu hỏi (cả bản có
dấu/không dấu), đo độ trễ từng lệnh gọi, cộng token để tính giá thực tế theo
bảng giá mục 1, và ghi kết quả ra `tests/hoa03_benchmark_results.md` — đó mới
là bảng số liệu thật để đưa vào mục 5 bên dưới.

## 5. Bảng số liệu thực đo — CHỜ ĐIỀN

> Chưa có số liệu — cần chạy `tests/hoa03_benchmark.py` với API key thật.
> Sau khi chạy xong, dán kết quả từ `tests/hoa03_benchmark_results.md` vào đây.

| Model                  | Độ trễ trung bình | Giá thực tế / 15 câu | Chất lượng trả lời (tự chấm 1-5) | Chất lượng truy xuất (embedding) |
| ---------------------- | ----------------- | -------------------- | -------------------------------- | -------------------------------- |
| gemini-2.5-flash       | _chưa đo_         | _chưa đo_            | _chưa đo_                        | —                                |
| gpt-4o-mini            | _chưa đo_         | _chưa đo_            | _chưa đo_                        | —                                |
| text-embedding-3-small | —                 | _chưa đo_            | —                                | _chưa đo_                        |
| gemini-embedding-001   | —                 | _chưa đo_            | —                                | _chưa đo_                        |

## 6. Khuyến nghị tạm thời (trước khi có số liệu thật)

- **Model chính (đề xuất tạm):** `gemini-2.5-flash` — vì có free tier thật,
  cho phép test/vận hành ở quy mô nhỏ mà không tốn tiền ngay từ đầu, đúng yêu
  cầu của bạn ("mặc định trước là gemini để free test").
- **Model dự phòng (đề xuất tạm):** `gpt-4o-mini` (OpenAI) — khác nhà cung cấp
  hoàn toàn với model chính, đúng tinh thần "model dự phòng" trong spec kiến
  trúc (nếu Gemini sập/quá tải, chuyển hẳn sang OpenAI chứ không phải model
  khác cùng nhà cung cấp cũng có thể sập cùng lúc).
- **Embedding:** để mặc định theo cùng nhà cung cấp với LLM chính (Gemini →
  `gemini-embedding-001`) cho đơn giản vận hành, nhưng **cần đo thật** vì OpenAI
  `text-embedding-3-small` rẻ hơn 7.5 lần — nếu chất lượng truy xuất tương
  đương, nên cân nhắc dùng OpenAI cho riêng phần embedding dù LLM chính là
  Gemini (2 thứ này độc lập, không bắt buộc cùng nhà cung cấp).

**Đây là đề xuất TẠM, dùng để bạn có cấu hình chạy được ngay (xem HOA-04) —
không phải kết luận cuối cùng của HOA-03.** Khi có số liệu thật ở mục 5, quay
lại xác nhận hoặc đổi.
