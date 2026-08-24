# H2-11 — Interface AI core đóng băng

Trạng thái: **đóng băng**

Tài liệu này là hợp đồng để backend tích hợp với AI core. Code nghiệp vụ
phía sau có thể thay đổi, nhưng chữ ký hàm và hình dạng dữ liệu trong tài liệu này
không được sửa âm thầm. Thay đổi phá vỡ tương thích phải tạo phiên bản interface mới.

## 0. Đã triển khai ở đâu?

| Thành phần                 | Vị trí trong code                                                     | Vai trò                                                        |
| -------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------- |
| Contract retrieval         | `RetrieverPort` — `ai_core/interfaces.py:38`                          | Khóa chữ ký `retrieve(query, tenant_id, k)`.                   |
| Contract chat              | `ChatPort` — `ai_core/interfaces.py:46`                               | Khóa chữ ký `chat(payload)`.                                   |
| Implementation thật        | `RealRetriever`, `RealChat` — `ai_core/interfaces.py:69-104`          | Gọi logic production hiện có.                                  |
| Implementation giả         | `InMemoryRetriever`, `InMemoryChat` — `ai_core/interfaces.py:133-240` | Chạy local/test, không gọi vector DB hoặc LLM.                 |
| Điểm chuyển implementation | `build_services()` — `ai_core/interfaces.py:243`                      | Đọc một biến config và tạo đồng thời retriever/chat cùng loại. |
| Một dòng config            | `.env.example:5`                                                      | `AI_CORE_INTERFACE_BACKEND=real` hoặc `in_memory`.             |
| Bằng chứng contract        | `tests/test_h2_11.py`                                                 | Cùng test chữ ký, schema và tenant cho cả hai implementation.  |

Backend chỉ import `build_services()` và gọi hai port. Backend không import
thẳng `ai_core.retriever.retrieve` hoặc `ai_core.chat.chat`, vì làm vậy sẽ bỏ qua lớp
interface đã đóng băng.

## 1. Retriever

```python
retrieve(query: str, tenant_id: str, k: int = 5) -> list[dict]
```

Quy tắc:

- `query` là câu hỏi cần tìm tri thức.
- `tenant_id` bắt buộc, không được thiếu, rỗng, sai định dạng hoặc không tồn tại.
- `k` là số nguyên dương; kết quả có tối đa `k` phần tử.
- Lọc `tenant_id` phải xảy ra trước khi xếp hạng. Kết quả của tenant A không bao
  giờ được chứa chunk tenant B.
- Mỗi kết quả tối thiểu có `tenant_id`, `chunk_id`, `content`, `metadata`, `score`.
- `metadata` theo schema `KnowledgeChunk`: `url`, `title`, `type`, `updated_at`.
- Request sai phải phát sinh lỗi rõ ràng, không được bỏ lọc tenant rồi trả dữ liệu chung.

Ví dụ:

```python
rows = services.retriever.retrieve(
    "gói website có SSL không",
    tenant_id="mima_internal",
    k=5,
)
```

## 2. Chat

```python
chat(payload: dict) -> dict
```

Payload bắt buộc:

```json
{
  "tenant_id": "mima_internal",
  "conversation_id": "2f44b9cd-4f86-4052-a749-33472eaeec4b",
  "message": "Gói website có SSL không?",
  "history": [],
  "config_version": 1
}
```

Response ổn định:

```json
{
  "reply": "...",
  "sources": [],
  "tool_calls": [],
  "need_human": false,
  "lead_captured": null,
  "guardrail": { "blocked": false, "reason": null },
  "usage": {
    "model": "...",
    "tokens_in": 0,
    "tokens_out": 0,
    "cached_tokens_in": 0,
    "cache_write_tokens_in": 0,
    "cost_usd": 0.0,
    "latency_ms": 0
  },
  "trace_id": "bb1d3134-2620-4e33-9b11-73f71e35f249"
}
```

`sources`, `tool_calls`, `need_human`, `lead_captured`, `guardrail`, `usage` và
`trace_id` luôn phải có để backend ghi log, lưu lead, debug và dựng dashboard.
Streaming là extension nội bộ của hàm core hiện hữu, không nằm trong interface
đóng băng H2-11. Backend của Hiếu chỉ tích hợp qua hai chữ ký công khai trong tài
liệu này, không gọi trực tiếp các tham số mở rộng của module core.

## 3. Hai implementation cùng một interface

Module [ai_core/interfaces.py](../ai_core/interfaces.py) cung cấp:

- `RetrieverPort`, `ChatPort`: Protocol đã đóng băng.
- `RealRetriever`, `RealChat`: gọi implementation production hiện tại.
- `InMemoryRetriever`, `InMemoryChat`: fake xác định, không gọi model/API, dùng cho
  unit test và cho Hiếu tích hợp khi backend thật chưa sẵn sàng.
- `build_services()`: factory đọc config rồi trả về một cặp implementation đồng nhất.

Luồng chọn implementation:

```text
.env: AI_CORE_INTERFACE_BACKEND
              |
              v
       build_services()
          /          \
       real        in_memory
        |               |
RealRetriever      InMemoryRetriever
RealChat           InMemoryChat
```

### 3.1. Đổi qua lại chỉ bằng một dòng config

Code khởi tạo bên backend giữ nguyên:

```python
from ai_core.interfaces import build_services

# Fixture chỉ được in_memory sử dụng; backend real bỏ qua các giá trị này.
services = build_services(
    tenant_ids=["mima_internal"],
    chunks=test_chunks,
    replies=test_replies,
)
```

Muốn chạy production, đặt một dòng trong `.env`:

```dotenv
AI_CORE_INTERFACE_BACKEND=real
```

hoặc:

```dotenv
AI_CORE_INTERFACE_BACKEND=in_memory
```

Muốn chạy fake thì chỉ đổi `real` thành `in_memory`, không sửa đoạn Python khởi tạo
phía trên. Khi ứng dụng khởi động lại, `build_services()` đọc biến môi trường và đổi
đồng thời cả retriever lẫn chat; không có trường hợp một phần thật, một phần giả.

Kiểm tra implementation đang được chọn:

```python
print(services.backend)            # "real" hoặc "in_memory"
print(services.interface_version)  # "h2-11.v2"
```

### 3.2. Backend gọi interface như thế nào?

Sau khi khởi tạo `services`, backend chỉ gọi:

```python
rows = services.retriever.retrieve(
    query="gói website có SSL không",
    tenant_id="mima_internal",
    k=5,
)

response = services.chat.chat(payload)
```

`payload` phải đúng schema tại Mục 2. Backend lưu `response`, đặc biệt `trace_id`,
`usage`, `sources`, `need_human` và `lead_captured`.

### 3.3. Hiếu thay implementation mà không sửa logic AI

Nếu Hiếu có hai hàm production mới nhưng giữ đúng chữ ký đã chốt, chỉ tiêm chúng ở
điểm khởi tạo:

```python
services = build_services(
    retrieve_implementation=hieu_retrieve,
    chat_implementation=hieu_chat,
)
```

Hai hàm của Hiếu phải có đúng dạng:

```python
def hieu_retrieve(query: str, tenant_id: str, k: int = 5) -> list[dict]: ...
def hieu_chat(payload: dict) -> dict: ...
```

Không sửa `ai_core.retriever`, `ai_core.chat`, prompt, guardrail hoặc RAG orchestration.
Nếu Hiếu chỉ thay vector store thật, giữ `AI_CORE_INTERFACE_BACKEND=real` và cấu hình
`AI_CORE_VECTOR_STORE_BACKEND=remote`; contract `retrieve()` và `chat()` vẫn giữ nguyên.

Fake không tự đoán tenant. Nếu không cấu hình tenant, hoặc truyền tenant lạ, request
phải lỗi. Điều này giúp test không che mất lỗi rò rỉ chéo tenant.

Chạy contract test dùng chung cho cả hai implementation:

```powershell
python -m unittest tests.test_h2_11 -v
```

## 4. Ranh giới tích hợp H2-17

- Backend chịu trách nhiệm xác thực người dùng, resolve tenant và lưu DB.
- Backend truyền đầy đủ lịch sử vào `payload.history` rồi gọi `chat(payload)`.
- Backend lưu response, đặc biệt `trace_id`, `usage`, `sources`, `tool_calls`,
  `need_human` và `lead_captured`.
- `ai_core` không import DB/ORM/HTTP framework và không tự đọc dữ liệu người dùng.
- `sources` và kết quả tool là dữ liệu cần kiểm tra/escape trước khi hiển thị ra UI.

## 5. Chính sách thay đổi

Được phép: bổ sung implementation mới phía sau Protocol hoặc bổ sung trường response
theo cách backend cũ vẫn đọc được. Không được phép trong phiên bản hiện hành: đổi tên/xóa tham số,
biến `tenant_id` thành tùy chọn, đổi kiểu trả về, hoặc xóa trường response. Mọi thay
đổi phá vỡ tương thích phải có version mới và contract test mới.
