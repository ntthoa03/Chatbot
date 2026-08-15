# Kết quả kiểm nghiệm HOA-03

Chạy API thật ngày 14/08/2026 trên 15 câu hỏi, mỗi câu gồm bản có dấu và
không dấu: 30 lượt trả lời cho mỗi stack.

| Hệ sinh thái | Model LLM + embedding | Chất lượng trả lời | Chất lượng truy xuất | Độ trễ E2E TB/câu | Giá thực tế toàn bộ lần test |
|---|---|---:|---:|---:|---:|
| OpenAI | `gpt-5.6-luna` + `text-embedding-3-small` | **28/30 đạt (93,33%)**, 4,867/5 | Hit@3 **80%**, Hit@1 70%, MRR 0,7852 | 2.212,6 ms | **$0,00554782 ≈ 144,24 VND** |
| Google Gemini | `gemini-3.5-flash-lite` + `gemini-embedding-001` | **28/30 đạt (93,33%)**, 4,867/5 | Hit@3 **100%**, Hit@1 80%, MRR 0,8833 | **1.127,6 ms** | $0,00929490 ≈ 241,67 VND |

## Số đo thành phần

| Stack | Query embedding TB | LLM TB | E2E TB | Dựng index một lần | Token embedding | Token LLM in/out | Tổng giá test |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenAI | 38,7 ms | 2.173,9 ms | 2.212,6 ms | 4.177,0 ms | 2.741 | 9.428 / 3.006 | $0,00554782 |
| Gemini | 60,3 ms | 1.067,3 ms | 1.127,6 ms | 2.811,8 ms | 1.726 | 8.921 / 2.544 | $0,00929490 |

## Định nghĩa

- Chất lượng trả lời: câu đạt khi được ≥4/5 theo rubric cố định trong
  `tests/h03_benchmark.py`.
- Chất lượng truy xuất: top-k chunk-id được so với ground-truth chunk-id khai
  báo trước; Hit@3 là tỷ lệ có chunk đúng trong ba kết quả đầu.
- Độ trễ E2E: query embedding trung bình + LLM trung bình, không gồm dựng index
  một lần.
- Giá thực tế: giá niêm yết nhân token usage/token count thực tế của lần test,
  gồm embedding corpus, 30 query embedding và 30 lượt LLM; không phải dự phóng.

Kết luận: chọn Gemini làm primary vì cùng chất lượng trả lời nhưng Hit@3 cao hơn
20 điểm phần trăm và E2E nhanh hơn khoảng 49%. OpenAI làm fallback vì rẻ hơn và
khác nhà cung cấp.
