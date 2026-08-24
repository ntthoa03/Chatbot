# MIMA Multi-tenant Chatbot

Chatbot tư vấn sử dụng RAG, guardrail hai lớp, hội thoại nhiều lượt, thu lead, chuyển người thật, tool calling, cache semantic và model routing. Logic AI nằm trong `ai_core/` và được thiết kế độc lập với DB/HTTP framework để backend có thể tích hợp qua `chat(payload)`.

> Hiện project dùng 40 chunk MIMA mẫu và local index để phát triển/eval. Trước khi chạy production phải thay bằng data và vector store thật.

## Bắt đầu nhanh trên Windows

### 1. Cài dependency

```powershell
python -m pip install -r requirements.txt
```

### 2. Tạo `.env`

```powershell
Copy-Item .env.example .env
```

Mở `.env` và tối thiểu điền API key của model đang dùng:

```dotenv
GEMINI_API_KEY=...
OPENAI_API_KEY=...

AI_CORE_UI_TENANT_ID=mima_internal
AI_CORE_UI_CONFIG_VERSION=1
AI_CORE_VECTOR_STORE_BACKEND=auto
```

Không commit `.env` hoặc secret thật vào Git.

### 3. Tạo lại local index khi cần

```powershell
python index_chunks.py --tenant-id mima_internal --input seed_chunks.json --out-dir index
```

Lệnh này đọc `seed_chunks.json`, tạo embedding và ghi `vectors.npy`, `metadata.json`, `manifest.json` vào `index/`.

### 4. Chạy UI nội bộ

```powershell
.\run_ui.ps1
```

Mở: [http://localhost:8501](http://localhost:8501)

## Các lệnh kiểm tra chính

```powershell
# Toàn bộ unit/integration test
python -m unittest discover -s tests -p "test_*.py" -v

# Bảng điểm eval tổng
python -m eval.run --cases eval/cases.yaml --tenant-id mima_internal

# Guardrail trap sau khi vá
python -m eval.run_h2_03 --suite trap

# Ma trận tinh chỉnh RAG H2-07
python -m eval.run_h2_07 --rebuild-indexes --run-id mima_real_v1 --workers 3 --requests-per-minute 15 --embedding-provider gemini --embedding-model gemini-embedding-001

# Xem feedback gần nhất từ UI
python -m ai_core.feedback --tail 20

# Thống kê người test
python -m ai_core.feedback --stats
```

## Gọi AI core từ backend

```python
from ai_core.chat import chat

response = chat({
    "tenant_id": "mima_internal",
    "conversation_id": "b3e1e2b0-1234-4a11-8b11-000000000001",
    "message": "Cho tôi bảng giá thiết kế website",
    "history": [],
    "config_version": 1,
})
```

Các trường quan trọng trong response:

- `reply`: câu trả lời cuối đã qua guardrail;
- `sources`: chunk RAG dùng để trả lời;
- `need_human`: có cần chuyển người thật hay không;
- `lead_captured`: tên/SĐT đã được khách xác nhận;
- `guardrail`: trạng thái kiểm duyệt;
- `usage`: model, token, chi phí USD và độ trễ;
- `trace_id`: mã dùng để debug một lượt chat.

Schema đầy đủ nằm trong [`contract.md`](contract.md).

## Cấu hình quan trọng

| Nhu cầu                                           | Chỗ cấu hình                                           |
| ------------------------------------------------- | ------------------------------------------------------ |
| Persona, model, top-k, threshold, pricing routing | `tenants/<tenant_id>.yaml`                             |
| Luật guardrail theo ngành                         | `guardrail_profiles/*.yaml`                            |
| Local/remote vector store                         | `.env` với `AI_CORE_VECTOR_STORE_BACKEND`              |
| UI đang chạy tenant nào                           | `.env` với `AI_CORE_UI_TENANT_ID`                      |
| Bật/tắt semantic cache                            | `.env` với `AI_CORE_SEMANTIC_CACHE_ENABLED`            |
| Bật semantic output guardrail                     | `.env` với `AI_CORE_OUTPUT_GUARDRAIL_SEMANTIC_ENABLED` |

Không thêm logic riêng khách hàng bằng `if tenant_id == ...` trong `ai_core/chat.py`; ưu tiên tenant YAML, guardrail profile và dữ liệu RAG.

## Trạng thái trước production

Backend tích hợp cần hoàn thiện:

- thay `seed_chunks.json` bằng data MIMA thật;
- embed/upsert vào vector store thật và cấu hình `RemoteVectorStore`;
- tạo HTTP endpoint gọi `ai_core.chat.chat()`;
- lấy/lưu lịch sử, usage, trace, lead và handoff trong DB;
- thay domain mock bằng API H-07 thật;
- chạy lại tenant isolation, retrieval eval, normal eval và trap eval.

`app.py`, local JSONL, ngrok, `seed_chunks.json` và `index/` hiện chỉ phục vụ phát triển hoặc kiểm thử, không phải hạ tầng production.

---

## Kiến trúc và bản đồ source code

### 1. Ranh giới kiến trúc

`ai_core/` là package AI dùng chung và không chứa DB, ORM hay HTTP framework. Backend chịu trách nhiệm xác thực, endpoint, lịch sử hội thoại, DB, CRM và lưu lead; AI core chỉ nhận payload rồi trả response theo contract.

```text
Streamlit app.py hoặc Backend/API
                |
                | ChatRequest
                | tenant_id, conversation_id, message,
                | history, config_version
                v
        ai_core.chat.chat(payload)
                |
                +--> config.py ---------> tenants/*.yaml
                |                         guardrail_profiles/*.yaml
                +--> guardrail/input.py
                +--> lead.py + router.py
                +--> cache.py
                +--> retriever.py
                |       +--> embedder.py
                |       +--> vector_store.py
                +--> prompt.py + Gemini/OpenAI
                +--> tools/
                +--> guardrail/output.py
                +--> trace.py
                |
                v
 ChatResponse: reply, sources, tool_calls, need_human,
 lead_captured, guardrail, usage, trace_id
                |
                +--> Backend lưu message/usage/lead/trace vào DB
```

### 2. Luồng một lượt chat

1. UI hoặc backend tạo payload theo `ChatRequest`.
2. `chat.py` dùng `models.py` validate payload.
3. `config.py` nạp tenant YAML và guardrail profile.
4. Input guardrail chạy trước retrieval và model.
5. `lead.py` đọc lịch sử để nhớ tên/SĐT và trạng thái xác nhận lead.
6. `router.py` chọn model primary/fallback và nhận diện chuyển người.
7. `cache.py` kiểm tra cache theo tenant nếu được bật.
8. `retriever.py` embed câu hỏi rồi query local index hoặc remote vector API.
9. `prompt.py` ghép persona, luật, lịch sử và nguồn RAG.
10. `chat.py` gọi model, thực thi tool và fallback model khi cần.
11. Output guardrail kiểm tra giá, cam kết, dữ kiện không grounded và luật cấm.
12. Response được đóng theo `ChatResponse` và ghi trace để debug/eval.
13. Backend bên ngoài lưu response, usage, lead và handoff vào DB/CRM.

### 3. Cây thư mục phân cấp

```text
Chatbot - miama/
|
+-- app.py                         # UI Streamlit nội bộ
+-- handoff.py                     # Ticket chuyển Sale ngoài AI core
+-- index_chunks.py                # Tạo local vector index từ chunk JSON
+-- crawl_chunks.py                # Crawler tham chiếu, không dùng cho MIMA production
+-- seed_chunks.json               # 40 chunk MIMA mẫu
+-- contract.md                    # Contract request/response/chunk
+-- requirements.txt               # Dependency Python
+-- .env.example                   # Biến môi trường mẫu
+-- run_ui.ps1                     # Chạy UI local
+-- run_ui_ngrok.ps1               # Chạy UI qua ngrok
|
+-- ai_core/                       # Logic AI dùng chung, độc lập DB/HTTP
|   +-- chat.py                    # Orchestrator trung tâm
|   +-- models.py                  # Schema Pydantic
|   +-- config.py                  # Nạp và validate tenant config
|   +-- interfaces.py              # Interface real/in-memory
|   +-- prompt.py                  # Sinh system prompt
|   +-- router.py                  # Chọn model và định tuyến intent
|   +-- lead.py                    # Thu, nhớ và xác nhận lead
|   +-- fallback.py                # Fallback an toàn khi thiếu dữ liệu
|   +-- cache.py                   # Semantic response cache
|   +-- trace.py                   # Trace, timing và redact secret
|   +-- feedback.py                # Feedback/log Sale khi test
|   +-- evaluator.py               # Engine eval
|   +-- embedder.py                # Adapter embedding
|   +-- retriever.py               # Retrieval API dùng chung
|   +-- vector_store.py            # Local/remote vector adapter
|   +-- guardrail/
|   |   +-- input.py               # Chặn trước RAG/LLM
|   |   +-- output.py              # Kiểm duyệt trước khi trả khách
|   |   +-- pricing.py             # Giá/ngân sách bằng quy tắc
|   |   +-- pricing_semantic.py    # Model nhỏ cho ngân sách khó
|   +-- tools/
|       +-- base.py                # Contract tool
|       +-- registry.py            # Đăng ký và thực thi tool
|       +-- check_domain.py        # Kiểm tra domain, hiện còn mock
|       +-- request_appointment.py # Yêu cầu đặt lịch tenant y tế
|
+-- tenants/                       # Cấu hình riêng từng tenant
|   +-- mima_internal.yaml
|   +-- phongkham_hyhy.yaml
|   +-- tenant_template.example.yaml
|
+-- guardrail_profiles/            # Luật dùng lại theo ngành
|   +-- common.yaml
|   +-- digital_agency.yaml
|   +-- medical_clinic.yaml
|
+-- eval/                          # Dataset và lệnh đánh giá
+-- tests/                         # Unit/integration/regression tests
+-- docs/                          # Tài liệu bổ sung nếu được bàn giao kèm
+-- index/                         # Local index sinh khi chạy
+-- outputs/                       # Trace/log/report sinh khi chạy

```

### 4. Trách nhiệm từng tầng trong `ai_core/`

#### 4.1. Contract và cấu hình

| File                    | Trách nhiệm                                                                                                       | Khi nào sửa?                                                                  |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `ai_core/models.py`     | Schema `KnowledgeChunk`, `ChatRequest`, `ChatResponse`, `Source`, `ToolCall`, `Lead`, `GuardrailResult`, `Usage`. | Chỉ sửa khi backend và AI thống nhất đổi contract; cập nhật test cùng lúc.    |
| `ai_core/config.py`     | Validate tenant, đọc YAML, merge guardrail profile, validate model/retrieval.                                     | Khi thêm trường cấu hình dùng chung; không hardcode dữ liệu riêng khách hàng. |
| `ai_core/interfaces.py` | Interface ổn định `real` và `in_memory`.                                                                          | Backend dùng `real`, unit test có thể dùng `in_memory`.                       |

Hai biến khác nhau:

- `AI_CORE_INTERFACE_BACKEND=real|in_memory`: chọn implementation interface nghiệp vụ/test.
- `AI_CORE_VECTOR_STORE_BACKEND=auto|local|remote`: chọn nơi query vector.

#### 4.2. Điều phối hội thoại

| File                  | Trách nhiệm                                                                          | Lưu ý                                                          |
| --------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `ai_core/chat.py`     | Orchestrator: history, RAG, model, tool, guardrail, lead, cache, trace và streaming. | Không đặt DB/API framework hoặc `if tenant_id == ...` vào đây. |
| `ai_core/router.py`   | Chọn model, nhận diện catalogue giá và intent chuyển người.                          | Không hardcode tên gói MIMA nếu có thể suy ra từ config/RAG.   |
| `ai_core/lead.py`     | Trích tên/SĐT, validate SĐT, đọc lịch sử và trả `lead_captured`.                     | Không tự lưu DB.                                               |
| `ai_core/fallback.py` | Fallback khi thiếu dữ liệu và lọc lựa chọn theo ngân sách có bằng chứng.             | Không tự thêm giá/dữ kiện.                                     |
| `ai_core/prompt.py`   | Sinh prompt đầy đủ và prompt rút gọn.                                                | Sửa prompt phải chạy lại eval.                                 |

#### 4.3. RAG, embedding và vector store

```text
chat.py
   +--> retriever.retrieve(query, tenant_id, k)
             +--> embedder.embed_texts(...)
             +--> vector_store
                     +-- LocalNumpyVectorStore --> index/
                     +-- RemoteVectorStore ----> Vector API thật
```

| File                      | Trách nhiệm                                                        | Điểm tích hợp production                                              |
| ------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------- |
| `ai_core/embedder.py`     | Adapter embedding Gemini/OpenAI.                                   | Document/query phải cùng model và dimension.                          |
| `ai_core/retriever.py`    | Embed query, chọn store, áp top-k/threshold và trả chunk.          | Giữ `retrieve(query, tenant_id, k)`; thiếu/sai tenant phải lỗi.       |
| `ai_core/vector_store.py` | Protocol vector store, local NumPy adapter và remote HTTP adapter. | Ráp vector endpoint thật qua `RemoteVectorStore`; lọc tenant hai lớp. |

#### 4.4. Guardrail

```text
User message --> guardrail/input.py --> RAG/model
Model reply  --> guardrail/output.py --> khách hàng
                         |
                         +--> pricing.py
                         +--> pricing_semantic.py
```

| File                            | Trách nhiệm                                                                                    | Nguyên tắc                                                       |
| ------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| `guardrail/input.py`            | Chặn injection, spam, nội dung nguy hiểm, quá 1.000 ký tự; nhận diện khách bức xúc.            | Mẫu rõ ràng chặn local trước RAG/LLM; test tránh false positive. |
| `guardrail/output.py`           | Chặn cam kết, hoàn tiền, tự giảm giá, lộ nội bộ, chê đối thủ, giá sai và claim không grounded. | Đây là lớp ưu tiên để vá lỗ hổng.                                |
| `guardrail/pricing.py`          | Phân biệt ngân sách khách và giá dịch vụ; hiểu `k/tr/m`, khoảng, `<`, `>`, không dấu.          | Quyền báo giá vẫn do config và RAG quyết định.                   |
| `guardrail/pricing_semantic.py` | Chuẩn hóa cách nói ngân sách khó bằng model nhỏ.                                               | Không biến giá khách tự nói thành giá dịch vụ đáng tin cậy.      |

#### 4.5. Tool

| File                           | Trách nhiệm                                               | Trạng thái                                                          |
| ------------------------------ | --------------------------------------------------------- | ------------------------------------------------------------------- |
| `tools/base.py`                | Contract args/result/error chung.                         | Nền tảng cho tool mới.                                              |
| `tools/registry.py`            | Đăng ký, kiểm tra quyền tenant, timeout và thực thi tool. | Chỉ gọi tool có trong `enabled_tools`.                              |
| `tools/check_domain.py`        | Validate và kiểm tra tên miền.                            | Hiện mock; production thay handler bằng H-07 thật nhưng giữ schema. |
| `tools/request_appointment.py` | Yêu cầu đặt lịch sơ bộ tenant y tế.                       | Không xác nhận lịch thật hoặc thu bệnh án.                          |

#### 4.6. Cache, trace và feedback

| File                   | Trách nhiệm                                                                       | Production                                                  |
| ---------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `ai_core/cache.py`     | Cache semantic trong RAM, key có `tenant_id + config_version`, similarity và TTL. | Nhiều instance cần shared cache nhưng vẫn phân vùng tenant. |
| `ai_core/trace.py`     | `trace_id`, timing, redact secret và JSONL trace.                                 | Backend lưu DB/observability.                               |
| `ai_core/feedback.py`  | Feedback Sale và xuất CSV khi test.                                               | Production chuyển storage sang DB.                          |
| `ai_core/evaluator.py` | Load case, chấm rubric, chạy song song và xuất report.                            | Các runner trong `eval/` dùng engine này.                   |

### 5. Cấu hình multi-tenant

```text
tenants/<tenant_id>.yaml
        +--> persona
        +--> guardrail profile
        +--> pricing routing
        +--> lead policy
        +--> model/embedding/retrieval
        +--> enabled_tools

guardrail_profiles/
        +--> common.yaml
        +--> profile theo ngành
```

Thêm tenant mới:

1. Copy `tenants/tenant_template.example.yaml` thành `tenants/<tenant_id>.yaml`.
2. Điền persona, contact, model, retrieval, pricing routing và tool.
3. Chọn/tạo guardrail profile theo ngành.
4. Chuẩn bị chunk và vector namespace có đúng `tenant_id`.
5. Chạy isolation test và eval riêng tenant.
6. Không thêm nhánh riêng tenant trong `chat.py`.

### 6. Cần ráp để lên production

#### 7.1. Data và vector store thật — H2-16

```text
Content API/data MIMA thật
        +--> map về KnowledgeChunk
        +--> embed document
        +--> upsert vector + metadata + tenant_id
        v
Vector DB thật
        ^
        +-- RemoteVectorStore.query(query_vector, tenant_id, k)
```

Các bước:

1. Map data thật về schema `KnowledgeChunk` trong `models.py`.
2. Embed/upsert theo namespace hoặc filter `tenant_id`.
3. Cung cấp query endpoint đúng contract của `RemoteVectorStore`.
4. Cấu hình:

```dotenv
AI_CORE_VECTOR_STORE_BACKEND=remote
AI_CORE_VECTOR_STORE_URL=https://<vector-api-that>
AI_CORE_VECTOR_STORE_API_KEY=<secret>
AI_CORE_VECTOR_STORE_TIMEOUT_SECONDS=10
```

5. Không fallback âm thầm sang local nếu remote production lỗi.
6. Chạy tenant isolation, remote smoke test và toàn bộ eval.

#### 7.2. Endpoint chat và DB — H2-17

Backend gọi `chat(payload)` và chịu trách nhiệm:

- xác thực user/tenant;
- lấy lịch sử từ DB và truyền vào `history`;
- lưu user/assistant message;
- lưu `sources`, `usage`, `trace_id`, `guardrail`, `need_human`;
- lưu `lead_captured` và đưa handoff vào hàng đợi Sale;
- không cho client tự chọn tenant trái phép;
- không đưa DB/ORM/HTTP framework vào `ai_core`.

### 8. Phần chưa phải production

- `seed_chunks.json`, `index/`: data/index demo local.
- `crawl_chunks.py`: crawler tham chiếu, không phải nguồn MIMA production.
- `mock_check_domain`: chưa gọi dịch vụ domain thật.
- Cache semantic: RAM của một process, restart sẽ mất.
- `trace.py`, `feedback.py`, `handoff.py`: đang ghi file local.
- `app.py`: UI nội bộ, không phải backend API.
- Log có `synthetic=true`: không phải log khách thật.
- Report eval cũ: phải chạy lại sau khi nối data thật.

### 9. Tra cứu nhanh

| Nhu cầu                       | File cần xem đầu tiên                             |
| ----------------------------- | ------------------------------------------------- |
| Persona/model/top-k/threshold | `tenants/<tenant>.yaml`                           |
| Thêm tenant                   | `tenants/` + profile + vector namespace           |
| Luật cấm theo ngành           | `guardrail_profiles/*.yaml`                       |
| Flow chat                     | `ai_core/chat.py`                                 |
| Prompt                        | `ai_core/prompt.py`                               |
| Model routing/catalogue       | `ai_core/router.py`                               |
| Injection/spam/bức xúc        | `ai_core/guardrail/input.py`                      |
| Output vi phạm                | `ai_core/guardrail/output.py`                     |
| Giá/ngân sách                 | `pricing.py`, `pricing_semantic.py`               |
| Retrieval                     | `ai_core/retriever.py`                            |
| Remote vector API             | `.env`, `ai_core/vector_store.py`                 |
| Lead/SĐT                      | `ai_core/lead.py`                                 |
| Tool mới                      | `tools/base.py`, file tool, registry, tenant YAML |
| Schema API                    | `models.py`, `contract.md`, backend và test       |
| Eval case                     | `eval/cases.yaml`                                 |
| Debug                         | tìm bằng `trace_id`                               |

### 10. Checklist bàn giao

- Không đưa DB/ORM/HTTP framework vào `ai_core`.
- Không hardcode `tenant_id == mima_internal`.
- Mọi vector query và cache key phải có tenant.
- Tenant thiếu/rỗng/sai phải lỗi.
- Không đổi schema request/response một phía.
- Không báo cáo production bằng seed/synthetic data.
- Sửa RAG/prompt/model/guardrail phải chạy lại eval.
- Giữ `sources`, `usage`, `trace_id`, `need_human`, `lead_captured` trong response.
- Secret chỉ nằm trong `.env` hoặc secret manager.
