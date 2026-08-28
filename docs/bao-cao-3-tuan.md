# Báo cáo kỹ thuật 3 tuần — MIMA Multi-tenant Chatbot

## Kết luận điều hành và quyết định đề xuất

> Trong ba tuần, đã xây được một AI core dùng chung và kiểm chứng trên 5 tenant thuộc 5 ngành, có cách ly dữ liệu, guardrail, đo chất lượng, chi phí và độ trễ. Run MIMA auto-routing đạt 79,66%, khoảng $0,0004216 và 2,06 giây mỗi lượt; run đa tenant đạt 89,33% hiệu dụng nhưng có hai lỗi hạ tầng. Kết quả đủ để đề nghị pilot nội bộ, chưa đủ để bán đại trà vì data MIMA, vector store, Postgres, auth, PII và tool realtime còn là bản tạm. Đề nghị cấp một backend/infra owner, một AI/RAG owner và đầu mối Sale để thay hạ tầng tạm, nạp dữ liệu thật và pilot 1–2 tenant có kiểm soát.

### Đã chứng minh và chưa chứng minh

| Đã có bằng chứng                                                                      | Chưa được phép kết luận                                                                        |
| ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 5 tenant thuộc 5 ngành chạy trên cùng AI core, không thêm nhánh hardcode theo tenant. | Chưa chứng minh hệ thống chịu tải production hoặc vận hành ổn định nhiều instance.             |
| Config, index, retrieval, cache và storage đều có kiểm tra cách ly tenant.            | Chưa chứng minh vector store production giữ nguyên điểm và isolation sau khi thay local index. |
| Có eval tự động, trace, chi phí USD, latency và phân loại lỗi theo tenant/topic.      | Điểm eval chưa đại diện tỷ lệ hài lòng khách thật vì corpus còn nhỏ và có dữ liệu synthetic.   |
| FastAPI `/chat`, streaming sau guardrail, SQLite persistence và widget demo đã chạy.  | Public key tĩnh, SQLite, CORS `*`, Streamlit/widget và ngrok không phải hạ tầng bán cho khách. |
| Guardrail red-team đạt 30/30 trap case trong run H2-03.                               | Guardrail chưa hết chặn nhầm; run H3-11 vẫn tìm thấy 2 false positive.                         |

### Hai bộ KPI phải báo cáo riêng, không trộn số

**KPI MIMA auto-routing — cùng một run 60 câu tuần 2:**

|     % đúng | Chi phí trung bình/lượt | Độ trễ trung bình | Ghi chú                                             |
| ---------: | ----------------------: | ----------------: | --------------------------------------------------- |
| **79,66%** |          **$0,0004216** |    **2,060 giây** | Tổng $0,0252953; 47 câu model rẻ, 13 câu model mạnh |

So với chạy model mạnh cho toàn bộ 60 câu, auto-routing tăng điểm từ 74,58% lên 79,66%, giảm chi phí **22,95%** và giảm latency khoảng 1,98 giây. Mục tiêu giảm chi phí 30% của H2-09 **chưa đạt**, nên không được báo cáo H2-09 là hoàn thành hoàn toàn.

**KPI đa tenant — cùng một run 75 câu tuần 3:**

|   % đúng hiệu dụng | % đúng trên case chấm được | Chi phí trung bình/lượt | Độ trễ trung bình |
| -----------------: | -------------------------: | ----------------------: | ----------------: |
| **67/75 = 89,33%** |         **67/73 = 91,78%** |          **$0,0013942** |    **4,086 giây** |

Hai case không chấm được là một `retrieval_error` và một `llm_error`; chúng được tính là không đạt trong tỷ lệ hiệu dụng 89,33%. Chi phí/latency tuần 3 cao hơn tuần 2 vì dataset, tenant và model mix khác; không được dùng bảng này để kết luận chi phí tăng do multi-tenant nếu chưa chạy A/B trên cùng corpus.

### Quyết định đề xuất

- **GO:** pilot có kiểm soát với MIMA và tối đa một tenant thật, có người duyệt dữ liệu và theo dõi lỗi hằng ngày.
- **NO-GO:** chưa mở production đại trà và chưa cam kết SLA/chất lượng thương mại.
- Cần backend-infra thay Content API/vector/Postgres/auth sau các interface đã chốt.
- Cần một đầu mối Sale/nghiệp vụ duyệt dữ liệu, giá, CTA, handoff và biến feedback sai thành case eval.
- Cần chốt chính sách PII: dữ liệu nào được lưu, thời gian lưu, quyền xem, mã hóa, backup và xóa.

---

## Kiến trúc đã dựng và kết quả theo từng tuần

### Kiến trúc tổng thể

```text
Content API (chưa nối thật)
  -> validate/chunk/hash -> embedding -> local/remote vector store
                                      |
Client/widget -> POST /chat -> resolve tenant
                              -> ChatPort.chat(payload)
                              -> config/template/guardrail profile
                              -> retriever + semantic cache + RAG
                              -> Gemini/OpenAI + tool registry
                              -> output guardrail + trace
                              -> ChatResponse
                                      |
                                      -> Storage -> SQLite tạm / Postgres tương lai
```

`ChatResponse` giữ các trường tích hợp quan trọng: `reply`, `sources`, `tool_calls`, `need_human`, `lead_captured`, `guardrail`, `usage` và `trace_id`. `ai_core/` không phụ thuộc FastAPI, DB hoặc ORM.

Ba ranh giới cần giữ:

- `retrieve(query, tenant_id, k)` — tenant bắt buộc, lọc trước ranking, trả chunk kèm source/score;
- `chat(payload)` — nhận `ChatRequest`, trả `ChatResponse` ổn định;
- `Storage` — API lưu conversation/message/lead/usage mà không biết SQLite hay Postgres.

Implementation AI đổi bằng `AI_CORE_INTERFACE_BACKEND`; vector local/remote đổi bằng `AI_CORE_VECTOR_STORE_BACKEND`; database đổi bằng `AI_API_STORAGE_BACKEND`. Mục tiêu của cấu trúc này là Hiếu thay hạ tầng phía sau interface mà không sửa prompt, guardrail hoặc hội thoại.

### Kết quả theo từng tuần

| Tuần                                   | Câu hỏi kỹ thuật cần trả lời                                                        | Kết quả chính                                                                                                                                                                           |
| -------------------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tuần 1 — dựng nền**                  | Có thể tạo một core RAG đo được chất lượng/chi phí và giữ contract ổn định không?   | Có `ChatRequest/ChatResponse`, retrieval, model comparison, eval một lệnh, trace/source, multi-turn, tool và interface `retrieve/chat`. Baseline 30 case đạt 70%.                       |
| **Tuần 2 — an toàn và tenant thứ hai** | Core có chịu được dữ liệu/ngôn ngữ thực tế, guardrail và tách tenant không?         | Sinh log khách tự nhiên, red-team/vá guardrail, phòng khám là tenant thứ hai, test isolation, cache theo tenant, UI feedback, lead/handoff, routing chi phí và spec bàn giao ingestion. |
| **Tuần 3 — mở rộng và demo**           | Kiến trúc có nhân lên 5 tenant, có API/storage/widget và đo chéo tenant được không? | 5 config + 5 index, template ngành, onboarding một lệnh/checkpoint, SQLite/API/widget demo, eval 75 case, rehearsal, phân loại lỗi Sale và tài liệu bàn giao production.                |

### Bằng chứng multi-tenant

| Tenant                     | Ngành          | Record trong index | Thời gian chạy onboarding ghi nhận | Giới hạn cần nói rõ                                          |
| -------------------------- | -------------- | -----------------: | ---------------------------------: | ------------------------------------------------------------ |
| `mima_internal`            | Digital agency |                 40 |                Không có số lịch sử | 40 chunk seed, chưa phải data MIMA production                |
| `phongkham_hyhy`           | Y tế           |                776 |                Không có số lịch sử | Crawl thử nghiệm tuần 2; cần chủ dữ liệu duyệt nội dung y tế |
| `bat_dong_san_phuoc_thinh` | Bất động sản   |                 52 |                       105,483 giây | Lần đầu không đạt ngưỡng 100 chunk; chạy lại với ngưỡng 40   |
| `giao_duc_haiyan`          | Giáo dục       |              1.369 |                       172,714 giây | Gemini embedding lỗi; fallback OpenAI                        |
| `thuc_pham_thien_minh`     | Thực phẩm      |                 41 |                        87,522 giây | Giới hạn crawl 50 trang sau bottleneck tenant giáo dục       |

Tenant thứ 5 chạy nhanh hơn tenant thứ 3, đạt tiêu chí H3-01. Tuy nhiên các số trên chỉ là **thời gian máy chạy pipeline crawl/index trong lần ghi nhận**, không bao gồm thời gian thương mại để xác nhận phạm vi website, duyệt nội dung, viết persona/guardrail, sửa dữ liệu và nghiệm thu với khách.

Isolation đã được kiểm tra ở tenant đúng/sai/thiếu/rỗng, local/remote store, cache và toàn bộ 20 cặp có hướng giữa 5 tenant. Nguyên tắc là fail-closed: thiếu/sai tenant phải lỗi, không được query tập chung. Bằng chứng hiện áp dụng cho implementation đang test; bắt buộc chạy lại khi thay vector DB, cache hoặc DB production.

---

## Eval, chi phí, thất bại và bài học

### Số liệu qua ba tuần

| Giai đoạn                | Phạm vi                  |                        Kết quả |                                          Chi phí |                Latency | Cách diễn giải đúng                                                           |
| ------------------------ | ------------------------ | -----------------------------: | -----------------------------------------------: | ---------------------: | ----------------------------------------------------------------------------- |
| Tuần 1 — baseline HOA-17 | 30 case MIMA             |                 21/30 = 70,00% |                 Báo cáo cũ chỉ có 11,86 VND/lượt |             1,669 giây | Mốc ban đầu; không đổi VND sang USD hồi tố vì thiếu bảng giá/token chuẩn hóa  |
| Tuần 1 — model bake-off  | 30 câu trả lời mỗi stack |          28/30 = 93,33% cả hai | OpenAI tổng $0,00554782; Gemini tổng $0,00929490 | 2,213 giây; 1,128 giây | Rubric/dataset khác baseline; dùng để so stack trong cùng thử nghiệm          |
| Tuần 2 — guardrail       | 30 trap + 60 normal      | Trap 30/30; normal 42/60 = 70% |                       Trap $0 vì không gọi model |            Trap 127 ms | Chứng minh lớp kiểm duyệt độc lập; normal 70% cảnh báo chặn nhầm/thiếu độ phủ |
| Tuần 2 — auto-routing    | 60 case MIMA             |                         79,66% |                                  $0,0004216/lượt |             2,060 giây | Bộ ba KPI MIMA dùng để báo cáo demo                                           |
| Tuần 3 — 5 tenant        | 75 case                  |               89,33% hiệu dụng |                                  $0,0013942/lượt |             4,086 giây | Có 2 lỗi hạ tầng; corpus đa ngành nhưng chỉ 15 case/tenant                    |

Không được vẽ các tỷ lệ này thành một đường tăng 70% → 79,66% → 89,33%, vì dataset, rubric, tenant và model mix khác nhau. Điều đã cải thiện chắc chắn là **năng lực đo lường**: từ một baseline MIMA sang so sánh model, trap/normal, routing và bảng điểm theo 5 tenant/topic.

### So sánh 5 tenant tuần 3

| Tenant       | Pass chấm được | Pass hiệu dụng/15 case | Điểm yếu quan sát được                                                  |
| ------------ | -------------: | ---------------------: | ----------------------------------------------------------------------- |
| Giáo dục     |   15/15 = 100% |                   100% | Chưa thấy lỗi trong 15 case; mẫu còn nhỏ                                |
| Phòng khám   |   14/14 = 100% |                 93,33% | 1 `llm_error`; không tính thành lỗi kiến thức nhưng vẫn ảnh hưởng khách |
| Thực phẩm    | 13/14 = 92,86% |                 86,67% | Nhóm snacks yếu; 1 `retrieval_error`                                    |
| MIMA         | 13/15 = 86,67% |                 86,67% | 2 lỗi, chủ yếu `blocked_output`                                         |
| Bất động sản |    12/15 = 80% |                    80% | Yếu nhất; nhóm services 66,67%                                          |

Không nên dùng 100% của giáo dục để cam kết chất lượng ngành giáo dục. Nó chỉ có nghĩa 15 case hiện tại chưa tìm ra lỗi; cần log khách thật và corpus lớn hơn.

### Những gì đã thử nhưng chưa đạt hoặc đã bỏ

- **Tuning RAG trên seed/synthetic:** thử từng biến chunk size, overlap, top-k và threshold. Threshold 0,50 tăng điểm từ 27,1% lên 61,0%, nhưng làm tăng model call/token/chi phí. Kết quả chưa đủ để chốt production; phải chạy H2-07 lại trên data MIMA thật.
- **Mục tiêu routing giảm 30% chi phí:** thực tế giảm 22,95%. Cơ chế vẫn đáng giữ vì điểm tăng 5,08 điểm phần trăm, nhưng tiêu chí chi phí chưa đạt.
- **Guardrail:** H2-03 đạt 30/30 trap, nhưng H3-11 chạy 20 câu thực tế chỉ đạt 16, tìm thấy 4 lỗi: 2 chặn nhầm, 1 hiểu sai ý, 1 trả lời khô cứng. Một lỗi ngân sách đã vá nhưng chưa được phép giảm số lỗi báo cáo nếu chưa rerun toàn bộ suite.
- **Crawler/onboarding:** bất động sản không đạt ngưỡng ban đầu; giáo dục gặp lỗi embedding; thử nghiệm khác 15 trang chỉ sinh 8/10 chunk. Checkpoint/fallback giúp không mất tiến độ, chưa giải quyết site JavaScript, không sitemap hoặc robots chặn.
- **Dữ liệu realtime:** `check_domain` vẫn là mock và ghi `authoritative=false`. Demo chỉ chứng minh tool contract, không chứng minh kết quả WHOIS thật.
- **Chi phí:** chưa có forecast theo concurrency, cache hit thật, tỷ lệ câu dùng model mạnh và lưu lượng từng tenant; chi phí eval không phải ngân sách vận hành tháng.

---

## Khoảng cách production, rủi ro và kế hoạch

### Giữ lại hay phải viết lại

| Thành phần                                                    | Trạng thái hiện tại                                               | Quyết định                                                               |
| ------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------ |
| AI core, prompt, guardrail, router, lead/handoff, eval, trace | Đã có test và tái sử dụng đa tenant                               | **Giữ**, tiếp tục regression                                             |
| `retrieve()`, `chat(payload)`, `Storage`                      | Interface đã tách/đóng băng                                       | **Giữ**, thay implementation phía sau                                    |
| Tenant YAML, template ngành, guardrail profile                | Đã chạy 5 ngành                                                   | **Giữ có review nghiệp vụ**                                              |
| 40 chunk MIMA + local NumPy index                             | Fixture kiểm thử                                                  | **Bản tạm — thay hoàn toàn** bằng Content API và vector store production |
| SQLite + `storage/sqlite_store.py`                            | Lưu demo local                                                    | **Bản tạm — thay hoàn toàn** bằng `PostgresStore(Storage)` và migration  |
| Public key JSON, CORS `*`, FastAPI sync                       | Chứng minh HTTP contract                                          | **Bản tạm — harden/thay** auth, runtime và CORS                          |
| Streaming chia full reply thành đoạn                          | An toàn vì sau guardrail nhưng chưa giảm time-to-first-token thật | **Bản tạm — quyết định lại**; không stream token thô                     |
| Cache trong RAM                                               | Đúng tenant trên một process                                      | **Bản tạm — thay** shared cache khi nhiều instance                       |
| JSONL trace/feedback, Streamlit, ngrok, widget/demo HTML      | Công cụ nội bộ                                                    | **Demo — thay** observability và UI sản phẩm                             |
| `check_domain` mock                                           | Tool contract chạy                                                | **Chưa nối thật** — thay handler bằng API H-07                           |

### Rủi ro còn tồn đọng

1. **Rò dữ liệu tenant — mức rất cao:** thay vector/cache/DB có thể phá isolation. Biện pháp: tenant ở credential, namespace, filter, cache key và SQL; chạy ma trận chéo trước mọi release.
2. **Sai hoặc thiếu dữ liệu — mức cao:** MIMA chỉ có 40 seed chunk; điểm hiện tại không đại diện production. Biện pháp: data owner duyệt nguồn, sync incremental, content hash, delete/tombstone và rerun eval.
3. **Guardrail chặn nhầm — mức cao:** giảm cơ hội tư vấn dù an toàn pháp lý. Biện pháp: trap suite và normal suite phải cùng đạt; review false positive từ Sale hằng ngày.
4. **PII và quyền truy cập — mức cao:** lead SQLite chưa có encryption, retention, backup hoặc RBAC. Biện pháp: Postgres production, secret manager, audit, retention/xóa và phân quyền.
5. **Độ ổn định provider — mức trung bình/cao:** H3-09 đã có retrieval/LLM error. Biện pháp: timeout, retry giới hạn, circuit breaker, cảnh báo và fallback có đo lường.
6. **Onboarding website — mức trung bình:** crawler chưa bao phủ site JS/robots/no-sitemap. Biện pháp: báo lỗi rõ, resume, phương án API/snapshot thay vì âm thầm thiếu dữ liệu.
7. **Độ tin cậy eval — mức trung bình:** 15 case/tenant và dữ liệu synthetic chưa đủ đại diện. Biện pháp: nhập log khách thật, rubric do nghiệp vụ duyệt, lưu version corpus/config/model.

### Kế hoạch và nguồn lực

**P0 — điều kiện mở pilot:** nối Content API → mapper/chunk/hash → embed/upsert/xóa → RemoteVectorStore; tạo Postgres sau `Storage`; thay auth/public key; giới hạn CORS; nối domain API thật; chạy lại isolation 5 tenant, 30 trap, normal suite và eval MIMA.

**P1 — pilot 1–2 tenant:** chọn tenant có data owner; theo dõi trace/cost/error; sửa services bất động sản, snacks thực phẩm và false positive MIMA; đặt ngưỡng chấp nhận riêng cho chất lượng, latency và chi phí.

**P2 — vận hành:** shared cache phân vùng tenant, dashboard usage/error, cảnh báo provider, backup/restore, quy trình feedback hằng ngày và canary trước khi mở tenant mới.

Nguồn lực tối thiểu:

- **1 backend/infra owner:** Content API, vector DB, Postgres, auth, deployment, monitoring và bảo mật.
- **1 AI/RAG owner:** giữ contract/core, eval, guardrail, routing và xử lý hồi quy.
- **1 Sale/nghiệp vụ bán thời gian:** duyệt dữ liệu/giá/CTA/handoff và chấm lỗi khó.
- **1–2 tenant pilot có data owner:** không mở thêm ngành trước khi khép vòng dữ liệu → eval → feedback → sửa.

## Phụ lục — nguồn kiểm chứng

- Contract/kiến trúc: `README.md`, `contract.md`, `docs/interfaces.md`, `docs/ingestion-spec.md`, `docs/ban-giao-cho-hieu.md`.
- Tuần 1: `outputs/hoa03/h03_summary.json`, `outputs/hoa17/.../baseline/*.json`.
- Guardrail: `outputs/h2_03/H2-03-summary.json`.
- RAG tuning: `outputs/h2_07/runs/20260820_full/h2_07_results.json` và `H2-07-khuyen-nghi.md`.
- Routing/cost: `outputs/h2_09/h2_09_comparison.json`.
- Multi-tenant/onboarding: `outputs/h3_01/index_catalog.json`, `outputs/h3_01/onboarding_times.csv`.
- Eval 5 tenant: `outputs/h3_09/comparison.json`, `outputs/h3_09/run-errors.json`.
- Lỗi Sale: `outputs/h3_11/h3_11_error_summary.json`.
- Demo/rehearsal: `outputs/h3_07/live-rehearsal-report.json`, `outputs/h3_07/rehearsal-report.json`.
