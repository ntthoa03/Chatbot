# Ingestion spec MIMA

## 1. Mục đích và phạm vi

Tài liệu mô tả cách đưa dữ liệu MIMA thật vào vector store production để Hiếu có thể ráp tiếp mà không phải tìm hiểu lại phần thử nghiệm.

- Nguồn crawl tham chiếu: [GitHub Chatbot](https://github.com/ntthoa03/Chatbot).
- `seed_chunks.json` chỉ có 40 chunk mẫu để kiểm tra schema và luồng local, không phải dữ liệu production.
- Dữ liệu thật do Hiếu cung cấp qua Content API hoặc snapshot có cấu trúc tương đương `seed_chunks.json`.
- `crawl_chunks.py` chỉ tham chiếu cách lấy/làm sạch HTML, không nằm trong runtime chat MIMA.

```text
Content API -> làm sạch/chunk -> KnowledgeChunk -> embedding -> vector store
Vector Query API -> RemoteVectorStore -> retriever -> chat
```

- **Vector Query API** phục vụ tìm kiếm lúc chat; URL này được gán vào `AI_CORE_VECTOR_STORE_URL`.

## 2. Định dạng dữ liệu theo từng bước

### 2.1. Đầu vào Content API

Mỗi item cần ID ổn định, tenant, nội dung, URL, tiêu đề, loại nội dung và ngày cập nhật:

```json
{
  "items": [
    {
      "id": "website-pricing",
      "tenant_id": "mima_internal",
      "content": "Bảng giá và mô tả đầy đủ...",
      "url": "https://mima.vn/bang-gia-thiet-ke-website",
      "title": "Bảng giá thiết kế website",
      "type": "pricing",
      "updated_at": "2026-08-21"
    }
  ],
  "next_cursor": null,
  "deleted_ids": []
}
```

Nếu API dùng tên trường khác, chỉ viết mapper về schema trên, không sửa luồng RAG. `deleted_ids` hoặc `deleted=true` dùng để xóa vector không còn hiệu lực.

### 2.2. Parse và chuẩn hóa

Đầu ra chưa có vector:

```json
{
  "source_id": "website-pricing",
  "tenant_id": "mima_internal",
  "clean_text": "Bảng giá và mô tả đầy đủ...",
  "url": "https://mima.vn/bang-gia-thiet-ke-website",
  "title": "Bảng giá thiết kế website",
  "type": "pricing",
  "updated_at": "2026-08-21"
}
```

### 2.3. Chia chunk

| Biến          |   Giá trị | Lý do                                                                                |
| ------------- | --------: | ------------------------------------------------------------------------------------ |
| Chunk size    | 900 ký tự | Đủ chứa một mục dịch vụ/bảng giá nhưng không kéo quá nhiều nội dung thừa vào prompt. |
| Overlap       | 120 ký tự | Giữ tên gói và giá/tính năng khi nằm ở ranh giới hai chunk.                          |
| Minimum chunk | 160 ký tự | Tránh tạo vector từ tiêu đề hoặc câu rời ít thông tin.                               |

Quy tắc chính:

- Ưu tiên cắt theo heading/đoạn/list; không tách tên gói khỏi giá và tính năng.
- Chunk cuối dưới 160 ký tự thì ghép vào chunk trước.
- API trả bài dài thì chia 900/120; trả sẵn chunk đúng schema thì không chia lần hai.
- `chunk_id` phải ổn định, sinh từ `tenant_id + source_id + vị trí logic`.
- Dùng `content_hash` để chỉ embed lại chunk mới hoặc thay đổi.

Cấu hình 900/120 đã chạy ổn với tenant phòng khám: 100 trang tạo 776 chunk, độ dài min/trung bình/max khoảng 197/766/1.015 ký tự. MIMA thật vẫn phải chạy lại H2-07 trước khi chốt.

### 2.4. Format `KnowledgeChunk`

Code kiểm tra schema này bằng `KnowledgeChunk` tại `ai_core/models.py:28-35`:

```json
{
  "tenant_id": "mima_internal",
  "chunk_id": "mima-website-pricing-001",
  "content": "Gói Website Basic có giá 2.000.000đ...",
  "metadata": {
    "url": "https://mima.vn/bang-gia-thiet-ke-website",
    "title": "Bảng giá thiết kế website - Gói Basic",
    "type": "pricing",
    "updated_at": "2026-08-21"
  }
}
```

File trung gian là JSON array giống `seed_chunks.json`. Không được thiếu `tenant_id`, trùng `(tenant_id, chunk_id)`, sai URL hoặc sai ngày `YYYY-MM-DD`.

### 2.5. Vector record

Model embed tài liệu phải trùng model embed câu hỏi. Policy hiện nằm tại `tenants/mima_internal.yaml:75-81`: primary `gemini-embedding-001`, fallback `text-embedding-3-small`.

```json
{
  "id": "mima-website-pricing-001",
  "namespace": "mima_internal",
  "values": [0.012, -0.034],
  "metadata": {
    "tenant_id": "mima_internal",
    "chunk_id": "mima-website-pricing-001",
    "content": "Gói Website Basic có giá 2.000.000đ...",
    "url": "https://mima.vn/bang-gia-thiet-ke-website",
    "title": "Bảng giá thiết kế website - Gói Basic",
    "type": "pricing",
    "updated_at": "2026-08-21"
  }
}
```

Upsert và query đều phải có `namespace=mima_internal` và filter `tenant_id=mima_internal`. Client phải lọc lại tenant của response trước khi đưa vào RAG.

## 3. Các bước ráp production thật từ đầu đến cuối

Ba luồng cần hoàn thiện:

```text
Luồng 1 — ingestion nền: API data -> validate -> embed -> upsert/xóa vector
Luồng 2 — runtime chat: câu hỏi -> query vector -> RAG -> chat response
Luồng 3 — persistence: backend /chat -> lưu message/usage/trace/lead vào DB
```

| Bước | File/điểm nối                                             | Cần làm cụ thể                                                                                                      | Điều kiện đạt                                                                                |
| ---: | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
|    1 | API data MIMA, ngoài repo                                 | Chốt sample response, phân trang/cursor, `updated_at`, ID ổn định và cách báo record đã xóa.                        | 100% sample data thật map được sang `KnowledgeChunk`.                                        |
|    2 | Ingestion worker H-06, ngoài repo                         | Tạo job chạy theo lịch/webhook; không tải toàn bộ Content API trong mỗi câu chat.                                   | Chạy lại không tạo record trùng.                                                             |
|    3 | Mapper H-06; `ai_core/models.py:28-35`                    | Map, làm sạch, chunk 900/120 nếu cần và validate từng item. Item lỗi đưa vào log/dead-letter, không upsert âm thầm. | Có tổng số nhận/đạt/lỗi và URL/ID để truy vết.                                               |
|    4 | Embedding worker H-06; `tenants/mima_internal.yaml:75-81` | Tính `content_hash`; chỉ embed chunk mới hoặc thay đổi.                                                             | Model/dimension document giống model/dimension query.                                        |
|    5 | Vector DB H-06; `ai_core/vector_store.py:246`             | Upsert idempotent theo `chunk_id`, namespace, tenant và metadata; xóa/tombstone record nguồn đã xóa.                | Thêm/sửa/xóa phản ánh đúng Content API.                                                      |
|    6 | Endpoint query H-06; `ai_core/vector_store.py:123-169`    | Cung cấp HTTP POST URL và API key đúng contract của `RemoteVectorStore`.                                            | Query trả match có `score` và metadata đầy đủ.                                               |
|    7 | `.env`; mẫu `.env.example:5,18-21`                        | Đặt interface `real`, vector backend `remote`, URL/key/timeout thật.                                                | `retrieve()` dùng remote, không đọc index 40 chunk.                                          |
|    8 | `tests/hoa15_remote_smoke.py` và tenant tests             | Test remote query, sai tenant, thiếu tenant và tenant rỗng.                                                         | Không rò chéo tenant; lỗi theo fail-closed.                                                  |
|    9 | Endpoint `/chat`; gọi service từ `ai_core/interfaces.py`  | Xác thực/resolve tenant, tải history DB và gọi `services.chat.chat(payload)`.                                       | Response giữ đủ schema H2-11.                                                                |
|   10 | DB production H2-17                                       | Lưu user/assistant message, `usage_events`, `trace_id`, `sources`, tool calls, `need_human`, `lead_captured`.       | Từ một `trace_id` truy được đúng hội thoại và usage.                                         |
|   11 | `ai_core/tools/check_domain.py:116-123` và API H-07       | Thay mock bằng adapter thật cho tên miền/lịch/giá động; không chờ vector sync.                                      | Tool có timeout/fallback và output được validate.                                            |
|   12 | `eval/run.py`, trace và monitoring                        | Chạy eval, 10 hội thoại E2E và canary tenant nội bộ trước khi mở rộng.                                              | Có % đúng, chi phí USD trung bình, latency trung bình và không hồi quy quá ngưỡng chấp nhận. |

### 3.1. Contract Vector Query API

`RemoteVectorStore` tại `ai_core/vector_store.py:140-169` gửi payload tương đương:

```json
{
  "vector": [0.012, -0.034, 0.056],
  "top_k": 20,
  "namespace": "mima_internal",
  "filter": { "tenant_id": { "$eq": "mima_internal" } },
  "include_metadata": true,
  "include_values": false
}
```

- `vector`: embedding câu hỏi do retriever tạo.
- `top_k`: adapter lấy dư ứng viên rồi lọc/cắt về `k`.
- `namespace` và `filter.tenant_id`: bắt buộc để cách ly tenant.
- Response phải có `matches` hoặc `results`; mỗi item có `score` và metadata đầy đủ.

### 3.2. Cấu hình runtime

```dotenv
AI_CORE_INTERFACE_BACKEND=real
AI_CORE_VECTOR_STORE_BACKEND=remote
AI_CORE_VECTOR_STORE_URL=https://<vector-query-endpoint>
AI_CORE_VECTOR_STORE_API_KEY=<secret>
AI_CORE_VECTOR_STORE_TIMEOUT_SECONDS=10
```

`AI_CORE_VECTOR_STORE_URL` là Vector Query API, không phải Content API. Không ghi key vào source code.

## 4. Kiểm tra local và chạy lại eval

`index_chunks.py` chỉ kiểm tra schema/embedding/retrieval local:

```powershell
python index_chunks.py --tenant-id mima_internal --input seed_chunks.json --out-dir index
```

Đầu ra local gồm `index/vectors.npy`, `index/metadata.json`, `index/manifest.json` và `index/embedding_cache.json`. Các file này không thay thế vector DB/API production.

Kiểm tra contract và cách ly tenant:

```powershell
python -m unittest tests.test_hoa15 tests.test_tenant_isolation -v
```

Sau khi nối data thật, chạy lại eval tổng, guardrail và ma trận RAG:

```powershell
python -m eval.run --cases eval/cases.yaml --tenant-id mima_internal --report-dir outputs/h2_16/eval_real --workers 3 --requests-per-minute 15 --time-budget-seconds 300
python -m eval.run_h2_03 --suite normal --tenant-id mima_internal --workers 3 --requests-per-minute 15
python -m eval.run_h2_03 --suite trap --tenant-id mima_internal --workers 3 --requests-per-minute 15
python -m eval.run_h2_07 --rebuild-indexes --run-id mima_real_v1 --workers 3 --requests-per-minute 15 --embedding-provider gemini --embedding-model gemini-embedding-001
```

Với H2-07, đổi `SOURCE_CHUNKS` trong `eval/run_h2_07.py` từ `seed_chunks.json` sang snapshot `KnowledgeChunk` lấy từ API. Ba số bắt buộc bàn giao là **% đúng, chi phí USD trung bình mỗi hội thoại và độ trễ trung bình**.

## 5. Những cách đã thử và không chốt

- Dùng 40 chunk seed làm kho thật: thiếu độ phủ, chỉ đủ demo.
- Coi `index_chunks.py` là pipeline production: không có incremental sync, upsert/xóa DB hay query API.
- Gọi Content API mỗi lượt chat: chậm và retrieval không ổn định.
- Sinh `chunk_id` ngẫu nhiên mỗi lần sync: tạo record trùng, khó cập nhật/xóa.
- Chunk 300/50: tạo nhiều vector và dễ vỡ ngữ cảnh giá/tính năng.
- Chunk 800/50: chưa cải thiện ổn định so với 900/120.
- Overlap 0 làm mất ngữ cảnh; overlap 100 chưa chứng minh tốt hơn 120 và tăng độ trễ.
- Top-k 8 hoặc threshold 0,75 dễ bỏ sót; threshold 0,50 tăng recall nhưng tăng nhiễu, token và chi phí.

Đây chỉ là kết quả từ seed/synthetic; phải chạy lại eval trên data thật trước khi chốt production.
