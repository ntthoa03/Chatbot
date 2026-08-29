# Bàn giao phần backend tạm

**Phạm vi:** phần storage H3-04, endpoint H3-05 và các interface đã chốt từ tuần 2.  
**Mục tiêu:** thay hạ tầng demo bằng production mà không sửa logic RAG, prompt, guardrail, lead hoặc hội thoại trong `ai_core`.

## 1. Trạng thái hiện tại

```text
Browser/curl
   -> POST /chat + X-Public-Key              api/main.py
   -> resolve tenant                         PublicKeyResolver (tạm)
   -> ChatPort.chat(payload)                  interface đã đóng băng
   -> ai_core.chat.chat(payload)              logic AI giữ lại
   -> Storage                                 interface lưu trữ giữ lại
   -> SQLiteStore                             implementation tạm
   -> JSON hoặc SSE sau output guardrail      endpoint tạm
```

Luồng đã được kiểm chứng bằng test: key hợp lệ phân giải đúng tenant; key thiếu/sai bị từ chối; response được validate; user message, assistant message, lead và usage đọc lại được từ SQLite; streaming luôn phát response sau output guardrail.

Đây là **flow pilot/demo**, chưa phải backend production. SQLite, public key tĩnh, CORS `*`, giả streaming và cách gọi AI đồng bộ đều phải được thay hoặc quyết định lại trước khi mở cho khách thật.

## 2. Interface đã chốt — không đổi âm thầm

### 2.1. AI core

File: `ai_core/interfaces.py`.

```python
retrieve(query: str, tenant_id: str, k: int = 5) -> list[dict]
chat(payload: dict) -> dict
```

- `RetrieverPort.retrieve()` bắt buộc có `tenant_id`; thiếu/rỗng/sai phải fail-closed.
- `ChatPort.chat()` nhận payload theo `ChatRequest` và trả đủ `ChatResponse` trong `contract.md`.
- Chuyển fake/real chỉ bằng `AI_CORE_INTERFACE_BACKEND=in_memory|real` qua `build_services()`.
- Nếu cần đổi request/response, tạo version interface và migration client; không sửa chữ ký tại chỗ.

### 2.2. Storage

File: `storage/base.py`.

`Storage` là ranh giới backend đang dùng. `api/main.py` chỉ gọi các method abstract: tenant, conversation, message, lead, usage và các hàm đọc lại. `SQLiteStore` hiện thực interface này; `PostgresStore` phải hiện thực cùng interface.

Không đưa ORM, SQL, connection hoặc transaction của Postgres vào `ai_core`. Nếu production cần method mới, bổ sung có version và contract test trước khi sửa endpoint.

### 2.3. HTTP

File: `api/main.py`; schema nằm tại `ai_core/models.py` và `contract.md`.

- `POST /chat`, header `X-Public-Key`, query `stream=true|false`.
- Endpoint chỉ: nhận → resolve tenant → gọi `ChatPort` → lưu `Storage` → trả response.
- Không chuyển retrieval, guardrail, lead/handoff hay pricing vào endpoint.
- SSE hiện phát các event `delta` và `done`; event `done` chứa nguyên `ChatResponse`.

## 3. Danh sách phần giữ lại, thay thế và Hiếu làm mới

| Nhóm                     | Thành phần                                              | File/điểm nối                                 | Việc cần làm                                                                             |
| ------------------------ | ------------------------------------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Giữ lại**              | `ChatRequest`, `ChatResponse`, `KnowledgeChunk`         | `ai_core/models.py`, `contract.md`            | Dùng làm schema trao đổi; không tạo schema backend khác nghĩa.                           |
| **Giữ lại**              | `RetrieverPort`, `ChatPort`, `build_services()`         | `ai_core/interfaces.py`                       | Backend gọi qua interface; không import thẳng module RAG riêng lẻ.                       |
| **Giữ lại**              | Logic AI đa tenant                                      | `ai_core/`                                    | Không nhét DB/HTTP vào core; tiếp tục chạy regression guardrail và isolation.            |
| **Giữ lại**              | `Storage` ABC và dependency injection                   | `storage/base.py`, `api/main.py:create_app()` | Postgres được inject qua factory, endpoint không biết driver/SQL.                        |
| **Giữ lại có migration** | Năm nhóm dữ liệu tenant/conversation/message/lead/usage | `storage/schema.sql`                          | Giữ ý nghĩa và `tenant_id`; chuyển sang migration Postgres phù hợp.                      |
| **Thay thế**             | `SQLiteStore` và file `.sqlite3`                        | `storage/sqlite_store.py`                     | Chỉ dùng demo; không di chuyển file SQLite thành DB production.                          |
| **Thay thế**             | Key tĩnh từ JSON env                                    | `api/main.py:PublicKeyResolver`               | Dùng auth/tenant resolver production có thu hồi, hết hạn và audit.                       |
| **Thay thế**             | CORS mặc định `*`                                       | `api/main.py:_cors_origins_from_env()`        | Chỉ cho domain frontend/widget thật.                                                     |
| **Thay thế/hoàn thiện**  | Ghi từng record rời                                     | `api/main.py:_persist_turn()`                 | Transaction hoặc outbox/idempotency theo `trace_id`.                                     |
| **Thay thế/hoàn thiện**  | AI sync trong endpoint async                            | `api/main.py:post_chat()`                     | Worker/thread/async adapter, timeout và giới hạn tải.                                    |
| **Quyết định lại**       | Giả streaming sau full response                         | `api/main.py:_response_events()`              | Có thể giữ để an toàn; nếu stream token thật phải buffer và kiểm duyệt trước phát.       |
| **Hiếu làm mới**         | `PostgresStore(Storage)`                                | tạo `storage/postgres_store.py`               | Triển khai đủ method, connection pool, transaction, idempotency và lỗi chuẩn hóa.        |
| **Hiếu làm mới**         | Migration/backup/retention/PII                          | backend production                            | Index theo tenant, mã hóa/secret, retention, backup/restore và quyền truy cập.           |
| **Hiếu làm mới**         | Auth và tenant resolution thật                          | backend/API gateway                           | Không tin `tenant_id` do body tự khai; đối chiếu tenant từ credential.                   |
| **Hiếu làm mới**         | Monitoring                                              | backend production                            | Log/metric theo `tenant_id`, `trace_id`, model, latency, cost và lỗi; không log PII thô. |

## 4. TODO đã đánh dấu trong code

| TODO                                              | Lý do                                                            |
| ------------------------------------------------- | ---------------------------------------------------------------- |
| `storage/base.py` — `TODO(Hieu/Postgres)`         | Khóa ranh giới để thay DB không kéo SQL vào AI core.             |
| `storage/factory.py` — `TODO(Hieu/Postgres)`      | Đây là điểm import/chọn `PostgresStore` duy nhất.                |
| `storage/sqlite_store.py` — `TODO(Hieu/Postgres)` | SQLite connection đơn chỉ phù hợp demo.                          |
| `storage/schema.sql` — `TODO(Hieu/Postgres)`      | Type/default/index SQLite phải thành migration Postgres.         |
| `api/main.py` — `TODO(Hieu/Auth)`                 | Public key JSON tĩnh không phải authentication production.       |
| `api/main.py` — `TODO(Hieu/Security)`             | CORS `*` mở quá rộng cho môi trường thật.                        |
| `api/main.py` — `TODO(Hieu/Postgres)`             | Một lượt ghi cần transaction/idempotency.                        |
| `api/main.py` — `TODO(Hieu/Runtime)`              | Lời gọi AI đồng bộ có thể khóa event loop.                       |
| `api/main.py` — `TODO(Hieu/Streaming)`            | Không được đổi thành stream token thô rồi vượt output guardrail. |

TODO là biển báo bàn giao, không phải yêu cầu sửa logic AI tại các vị trí đó.

## 5. Thứ tự Hiếu tiếp quản

### Bước 1 — khóa baseline trước khi thay

Chạy:

```powershell
python -m unittest tests.test_h3_04 tests.test_h3_05 tests.test_hoa15 tests.test_isolation_multi -v
```

Lưu kết quả hiện tại. Không bắt đầu từ việc sửa `ai_core/chat.py`.

### Bước 2 — dựng Postgres sau `Storage`

1. Tạo migration cho năm nhóm dữ liệu, mọi bảng/bản ghi nghiệp vụ có `tenant_id`.
2. Tạo `storage/postgres_store.py` với `class PostgresStore(Storage)`.
3. Triển khai đủ method abstract, lọc tenant ngay trong mọi query.
4. Gói một lượt chat trong transaction hoặc cơ chế idempotent theo `trace_id`.
5. Viết contract test chạy cùng tình huống của `tests/test_h3_04.py`.

`storage/factory.py` đã có nhánh import trễ. Khi implementation tồn tại, cấu hình:

```dotenv
AI_API_STORAGE_BACKEND=postgres
AI_API_POSTGRES_DSN=<secret-from-secret-manager>
```

Không sửa `api/main.py` để chọn DB và không commit DSN.

### Bước 3 — thay tenant resolver/auth

Inject resolver production vào `create_app(public_key_resolver=...)` hoặc thay bằng adapter cùng trách nhiệm. Tenant lấy từ credential là nguồn tin cậy; nếu body gửi tenant khác phải trả 403. Bổ sung hết hạn/thu hồi key, rate limit và audit không chứa secret.

### Bước 4 — harden endpoint

- Đặt `AI_API_CORS_ORIGINS` thành danh sách domain thật, bỏ `*`.
- Chuyển lời gọi AI sang execution model không khóa event loop; đặt timeout/concurrency limit.
- Chọn giả streaming an toàn hoặc streaming có buffer kiểm duyệt. Tuyệt đối không phát nội dung trước output guardrail.
- Chuẩn hóa lỗi client/server nhưng không trả stack trace, DSN hoặc system prompt.

### Bước 5 — nối ingestion/vector production

Thực hiện theo `docs/ingestion-spec.md`: Content API → validate/chunk/hash → embed → upsert/xóa → remote query. Đây là luồng khác DB hội thoại; `AI_API_POSTGRES_DSN` không thay cho Vector Query API.

### Bước 6 — kiểm thử trước cutover

Chạy lại test ở Bước 1 với Postgres và auth adapter, sau đó:

```powershell
python -m unittest tests.test_h3_09 tests.test_isolation_multi -v
python -m eval.run --cases eval/cases_mima_internal.yaml --tenant-id mima_internal --report-dir outputs/h3_12/eval_mima
```

Kiểm tra thủ công một `trace_id`: request → hai message → usage → lead nếu có đều thuộc đúng tenant. Test thêm key sai, tenant body sai, rollback giữa lượt ghi và retry cùng `trace_id`.

### Bước 7 — cutover có rollback

Mở canary cho tenant nội bộ trước. Giữ khả năng chuyển `AI_API_STORAGE_BACKEND` về SQLite chỉ trong môi trường demo; production rollback bằng migration/deployment và backup Postgres, không dùng SQLite làm phương án dự phòng dữ liệu khách.

## 6. Điều kiện hoàn thành

- [ ] `PostgresStore` vượt cùng contract test của `SQLiteStore`.
- [ ] Sai/thiếu/rỗng tenant fail-closed; không đọc hoặc ghi chéo tenant.
- [ ] Một lượt chat được commit nguyên tử hoặc retry idempotent bằng `trace_id`.
- [ ] Auth resolver thật thay mapping JSON; body không thể tự chọn tenant khác.
- [ ] CORS chỉ cho domain được duyệt; secret không nằm trong repo/log.
- [ ] Streaming không phát nội dung chưa kiểm duyệt.
- [ ] API vẫn mỏng và `ChatResponse` không đổi schema.
- [ ] Có backup/restore, retention PII, monitoring và cảnh báo lỗi.
- [ ] Eval, guardrail và isolation không hồi quy so với baseline đã lưu.

## 7. Bẫy phải tránh

1. **Không sửa AI core để nối Postgres.** Đúng điểm thay là `PostgresStore` và factory.
2. **Không coi public key demo là authentication.** Nó chỉ chứng minh tenant resolution.
3. **Không bỏ `tenant_id` khỏi query/index.** Đây là nguyên nhân rò dữ liệu kinh điển.
4. **Không stream token thô trước guardrail.** Tốc độ không được đánh đổi rủi ro pháp lý.
5. **Không báo “đã production” chỉ vì API và SQLite chạy được.** Data MIMA, vector store, auth, Postgres, PII và monitoring vẫn cần hoàn thiện.
6. **Không đổi contract âm thầm.** Nếu cần đổi, version hóa và chạy consumer/contract test trước cutover.

Tài liệu chi tiết liên quan: `docs/interfaces.md`, `docs/ingestion-spec.md`, `contract.md`.
