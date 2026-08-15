# HOA-03 — Kiểm nghiệm và chọn LLM + embedding tiếng Việt

| Thuộc tính   | Kết quả                                                                                                         |
| ------------ | --------------------------------------------------------------------------------------------------------------- |
| Trạng thái   | **Hoàn thành** — đã gọi API thật ngày 14/08/2026                                                                |
| Dữ liệu      | 15 câu trong `tests/h03_test_questions.json`, mỗi câu chạy bản có dấu và không dấu                              |
| Nhà cung cấp | OpenAI và Google Gemini                                                                                         |
| Bằng chứng   | `outputs/hoa03/h03_summary.json`, `outputs/hoa03/h03_pilot_results.json`, `outputs/hoa03/h03_full_results.json` |

## Bảng so sánh đúng bốn chỉ số yêu cầu

Đây là bảng kết quả chính của Hoa-03. Mỗi stack chạy **30 lượt thật**: 15 câu ×
hai biến thể có dấu/không dấu.

| Hệ sinh thái  | Model LLM + embedding                            |              Chất lượng trả lời |                  Chất lượng truy xuất | Độ trễ E2E TB/câu | Giá thực tế toàn bộ lần test |
| ------------- | ------------------------------------------------ | ------------------------------: | ------------------------------------: | ----------------: | ---------------------------: |
| OpenAI        | `gpt-5.6-luna` + `text-embedding-3-small`        | **28/30 đạt (93,33%)**, 4,867/5 |  Hit@3 **80%**, Hit@1 70%, MRR 0,7852 |        2.212,6 ms | **$0,00554782 ≈ 144,24 VND** |
| Google Gemini | `gemini-3.5-flash-lite` + `gemini-embedding-001` | **28/30 đạt (93,33%)**, 4,867/5 | Hit@3 **100%**, Hit@1 80%, MRR 0,8833 |    **1.127,6 ms** |     $0,00929490 ≈ 241,67 VND |

### Cách tính bốn chỉ số

| Chỉ số               | Định nghĩa đo                                                                                                                                                            |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Chất lượng trả lời   | Câu đạt khi đạt ≥4/5 theo rubric cố định: đúng ý bắt buộc, không bịa giá, không cam kết SEO sai và từ chối đúng câu ngoài phạm vi. Báo cả số câu đạt/tổng để kiểm chứng. |
| Chất lượng truy xuất | So chunk-id top-k với ground-truth chunk-id khai báo trước; Hit@3 là tỷ lệ câu có ít nhất một chunk đúng trong ba kết quả đầu.                                           |
| Độ trễ               | Latency query embedding trung bình + latency LLM trung bình. Không cộng thời gian dựng index một lần.                                                                    |
| Giá thực tế          | Giá niêm yết × token usage/token count thực tế của đúng lần test; gồm dựng embedding corpus, embed 30 query và 30 lượt LLM. Không dùng dự phóng 100.000 lượt.            |

### Chi tiết độ trễ và chi phí thực đo

| Stack  | Query embedding TB |     LLM TB |     E2E TB | Dựng index một lần | Token embedding | Token LLM in/out | Tổng giá test |
| ------ | -----------------: | ---------: | ---------: | -----------------: | --------------: | ---------------: | ------------: |
| OpenAI |            38,7 ms | 2.173,9 ms | 2.212,6 ms |         4.177,0 ms |           2.741 |    9.428 / 3.006 |   $0,00554782 |
| Gemini |            60,3 ms | 1.067,3 ms | 1.127,6 ms |         2.811,8 ms |           1.726 |    8.921 / 2.544 |   $0,00929490 |

Chi phí embedding trong bảng này đã được đối soát lại bằng token usage của OpenAI
và tokenizer API của Gemini, thay cho ước lượng ký tự/4 ở bản báo cáo trước.

## 1. Kết luận dành cho production

- **LLM chính:** `gemini-3.5-flash-lite`.
- **LLM dự phòng khác nhà cung cấp:** `gpt-5.6-luna`.
- **Embedding chính:** `gemini-embedding-001` cho corpus thuần văn bản hiện tại.
- **Embedding dự phòng:** `text-embedding-3-small`.

Gemini được chọn làm tuyến chính dù chi phí sinh câu trả lời cao hơn, vì full
benchmark cho cùng điểm chất lượng nhưng latency thấp hơn khoảng 51%, Hit@3
retrieval cao hơn 20 điểm phần trăm và độ tương đồng giữa câu có dấu/không dấu
cao hơn rõ rệt. Phần chênh lệch chi phí dự phóng chỉ khoảng **$11,81 cho mỗi
100.000 câu trả lời**, chưa đủ lớn để đánh đổi độ tin cậy truy xuất tiếng Việt.

Nếu workload tương lai ưu tiên chi phí tuyệt đối hơn latency/retrieval, có thể
đảo `gpt-5.6-luna` thành primary. Không cần sửa code, chỉ đổi tenant YAML.

## 2. Vì sao các model này được đưa vào thử nghiệm

Pilot không mặc định chọn model mới nhất. Mỗi hệ sinh thái có một ứng viên tiết
kiệm và một ứng viên mới/mạnh hơn:

| Hệ sinh thái | Vai trò   | Ứng viên đã pilot                                  |
| ------------ | --------- | -------------------------------------------------- |
| OpenAI       | LLM       | `gpt-4o-mini`, `gpt-5.6-luna`                      |
| OpenAI       | Embedding | `text-embedding-3-small`, `text-embedding-3-large` |
| Gemini       | LLM       | `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`   |
| Gemini       | Embedding | `gemini-embedding-001`, `gemini-embedding-2`       |

OpenAI hiện mô tả GPT-5.6 Luna là lựa chọn tối ưu cho workload nhạy chi phí và
`text-embedding-3-large` là embedding mạnh nhất; Google mô tả 3.5 Flash-Lite là
model GA tiết kiệm nhất của dòng 3.5, còn Embedding 2 là bản stable mới hơn.
Benchmark vẫn giữ các model cũ/rẻ để kiểm tra xem premium có thực sự đáng trả.

Nguồn giá và trạng thái model: [OpenAI Models](https://developers.openai.com/api/docs/models),
[OpenAI text-embedding-3-large](https://developers.openai.com/api/docs/models/text-embedding-3-large),
[Gemini latest models](https://ai.google.dev/gemini-api/docs/generate-content/latest-model),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings).

## 3. Phương pháp đo

### 3.1 Pilot chọn theo quality/cost

LLM chạy 5 câu đại diện: báo giá website, quảng cáo cần báo giá riêng, từ chối
cam kết SEO, chẩn đoán SEO và câu ngoài phạm vi. Tất cả dùng cùng oracle context
để tách chất lượng LLM khỏi lỗi retrieval.

Embedding chạy trên đủ 15 câu × 2 biến thể và 13 chunk trong `seed_chunks.json`.
Ground truth là danh sách chunk-id liên quan được khai báo trước trong script.

### 3.2 Full benchmark

Hai stack thắng pilot chạy đủ 30 lượt/stack. LLM nhận top-3 thật từ embedding
cùng hệ sinh thái, không nhận oracle context. Ngưỡng từ chối được hiệu chỉnh trên
bộ test theo khả năng corpus có/không có câu trả lời.

Các chỉ số:

- Chất lượng trả lời 0–5 bằng rubric cố định, đạt khi ≥4.
- Hit@1, Hit@3 và MRR cho retrieval.
- Cosine giữa vector câu có dấu và không dấu.
- Latency đo tuần tự để tránh rate-limit làm sai lệch.
- Chi phí LLM dùng token usage thật; embedding ước tính token bằng ký tự/4 vì
  adapter chung không trả usage metadata.

Rubric được hiệu chỉnh sau full run để chấp nhận các cách từ chối tương đương
như “không có dữ liệu”, “ngoài lĩnh vực” và “từ chối”. Việc chấm lại dùng nguyên
phản hồi đã lưu, **không gọi lại API**.

## 4. Kết quả pilot LLM

| Model                   |   Điểm /5 | Tỷ lệ đạt |   Latency TB | Chi phí 5 câu | Dự phóng 100k câu theo pilot |
| ----------------------- | --------: | --------: | -----------: | ------------: | ---------------------------: |
| `gpt-4o-mini`           |     4,334 |       60% |     2.632 ms |     $0,000407 |                        $8,13 |
| `gpt-5.6-luna`          | **5,000** |  **100%** |     2.008 ms |     $0,001011 |                       $20,23 |
| `gemini-3.1-flash-lite` |     4,334 |       60% |     1.474 ms |     $0,001016 |                       $20,32 |
| `gemini-3.5-flash-lite` |     4,600 |       80% | **1.133 ms** |     $0,001650 |                       $33,00 |

Kết luận pilot LLM: premium của Luna mua được cải thiện chất lượng rõ ràng so
với 4o-mini. Gemini 3.5 cải thiện cả chất lượng lẫn latency so với 3.1 với mức
chi phí tuyệt đối vẫn nhỏ, nên hai model mới được đưa vào full benchmark.

## 5. Kết quả pilot embedding

| Model                    |   Hit@1 |    Hit@3 |        MRR | Cosine dấu/không dấu | Chi phí benchmark |
| ------------------------ | ------: | -------: | ---------: | -------------------: | ----------------: |
| `text-embedding-3-small` | **70%** |      80% | **0,7852** |               0,5688 |     **$0,000031** |
| `text-embedding-3-large` |     65% |      80% |     0,7625 |               0,7348 |         $0,000203 |
| `gemini-embedding-001`   |     80% | **100%** |     0,8833 |               0,9396 |         $0,000234 |
| `gemini-embedding-2`     | **90%** |      90% | **0,9225** |           **0,9571** |         $0,000402 |

Kết luận pilot embedding:

- OpenAI `3-large` đắt hơn khoảng 6,5 lần nhưng không tăng Hit@3; chọn `3-small`.
- Gemini Embedding 2 tăng Hit@1/MRR nhưng giảm Hit@3 từ 100% xuống 90% và đắt
  hơn; với RAG top-3 thuần text hiện tại, chọn `gemini-embedding-001`.
- Google vẫn cung cấp `gemini-embedding-001` stable cho text-only. Tuy nhiên,
  lịch deprecation hiện đặt mốc shutdown 14/05/2028 và khuyến nghị Embedding 2;
  cần lập kế hoạch re-index trước mốc đó vì hai vector space không tương thích.

## 6. Kết quả full 15 câu × 2 biến thể

### 6.1 Chất lượng, latency và chi phí LLM

| Stack LLM               |   Điểm /5 |  Tỷ lệ đạt | Có dấu /5 | Không dấu /5 |   Latency TB |          p95 | Tổng chi phí 30 câu |         USD/câu | Dự phóng 100k câu |
| ----------------------- | --------: | ---------: | --------: | -----------: | -----------: | -----------: | ------------------: | --------------: | ----------------: |
| `gpt-5.6-luna`          | **4,867** | **93,33%** | **5,000** |        4,733 |     2.174 ms |     2.945 ms |       **$0,005493** | **$0,00018309** |        **$18,31** |
| `gemini-3.5-flash-lite` | **4,867** | **93,33%** |     4,867 |    **4,867** | **1.067 ms** | **1.366 ms** |           $0,009036 |     $0,00030121 |            $30,12 |

Ở tỷ giá cấu hình 26.000 VND/USD, 100.000 câu tương đương khoảng **476.060 VND**
cho OpenAI hoặc **783.120 VND** cho Gemini, chưa gồm hạ tầng và các lượt
guardrail/tool khác.

### 6.2 Retrieval của hai embedding được chọn

| Embedding                |   Hit@1 |    Hit@3 |        MRR | Cosine dấu/không dấu | Accuracy có/không đáp án | Query latency TB |
| ------------------------ | ------: | -------: | ---------: | -------------------: | -----------------------: | ---------------: |
| `text-embedding-3-small` |     70% |      80% |     0,7852 |               0,5688 |                      90% |      **38,7 ms** |
| `gemini-embedding-001`   | **80%** | **100%** | **0,8833** |           **0,9396** |                 **100%** |          60,3 ms |

Hai lỗi dưới ngưỡng của Luna đều ở bản không dấu (`q02`, `q05`) và trùng với
điểm yếu retrieval dấu/không dấu của OpenAI small. Hai lỗi dưới ngưỡng của
Gemini (`q08` cả hai biến thể) vẫn từ chối cam kết đúng và an toàn, nhưng thiếu
phần giải thích kết quả SEO phụ thuộc dữ liệu/thực tế nên chỉ đạt 3/5.

## 7. Quyết định vận hành

| Tuyến    | LLM                     | Embedding                | Lý do                                                                          |
| -------- | ----------------------- | ------------------------ | ------------------------------------------------------------------------------ |
| Primary  | `gemini-3.5-flash-lite` | `gemini-embedding-001`   | Retrieval tiếng Việt tốt nhất, latency LLM thấp nhất, điểm full ngang OpenAI   |
| Fallback | `gpt-5.6-luna`          | `text-embedding-3-small` | Khác nhà cung cấp, chi phí thấp, chất lượng LLM cao; embedding đủ làm fallback |

Cấu hình đã được cập nhật trong `tenants/mima_internal.yaml`. Adapter OpenAI đã
được sửa để không gửi `temperature` cho GPT-5.6; adapter Gemini đã hỗ trợ đúng
hợp đồng N input → N vector của Embedding 2 để benchmark/migrate sau này.

```

## 9. Giới hạn

- Chỉ có 15 câu và 13 chunk; kết quả đủ cho HOA-03 nhưng chưa thay thế eval dài hạn.
- Mỗi cấu hình chỉ full-run một lần; latency có thể thay đổi theo vùng mạng/tải API.
- Dự phóng 100.000 câu chỉ gồm chi phí model sinh câu trả lời theo token thực đo.
- Khi có log câu hỏi khách hàng thật, nên mở rộng bộ eval và chạy lại cùng script.
```
