# H2-11 — Interface AI core v1

Trạng thái: **đóng băng**  
Phiên bản: `h2-11.v2`

Tài liệu này là hợp đồng để backend của Hiếu tích hợp với AI core. Code nghiệp vụ
phía sau có thể thay đổi, nhưng chữ ký hàm và hình dạng dữ liệu trong tài liệu này
không được sửa âm thầm. Thay đổi phá vỡ tương thích phải tạo phiên bản interface mới.

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
  "guardrail": {"blocked": false, "reason": null},
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
Streaming là extension của hàm core hiện hữu, không nằm trong interface H2-11 v1.

## 3. Hai implementation cùng một interface

Module [ai_core/interfaces.py](../ai_core/interfaces.py) cung cấp:

- `RetrieverPort`, `ChatPort`: Protocol đã đóng băng.
- `RealRetriever`, `RealChat`: gọi implementation production hiện tại.
- `InMemoryRetriever`, `InMemoryChat`: fake xác định, không gọi model/API, dùng cho
  unit test và cho Hiếu tích hợp khi backend thật chưa sẵn sàng.
- `build_services()`: factory trả về một cặp implementation đồng nhất.

Chỉ đổi **một dòng** trong `.env` để chuyển implementation:

```dotenv
AI_CORE_INTERFACE_BACKEND=real
```

hoặc:

```dotenv
AI_CORE_INTERFACE_BACKEND=in_memory
```

Khởi tạo production:

```python
from ai_core.interfaces import build_services

services = build_services()
result = services.chat.chat(payload)
```

Khởi tạo fake có tenant rõ ràng:

```python
services = build_services(
    backend="in_memory",
    tenant_ids=["mima_internal"],
    chunks=[chunk_fixture],
    replies={
        ("mima_internal", "xin chao"): "Dạ, MIMA xin chào anh/chị ạ."
    },
)
```

Fake không tự đoán tenant. Nếu không cấu hình tenant, hoặc truyền tenant lạ, request
phải lỗi. Điều này giúp test không che mất lỗi rò rỉ chéo tenant.

## 4. Ranh giới tích hợp H2-17

- Backend chịu trách nhiệm xác thực người dùng, resolve tenant và lưu DB.
- Backend truyền đầy đủ lịch sử vào `payload.history` rồi gọi `chat(payload)`.
- Backend lưu response, đặc biệt `trace_id`, `usage`, `sources`, `tool_calls`,
  `need_human` và `lead_captured`.
- `ai_core` không import DB/ORM/HTTP framework và không tự đọc dữ liệu người dùng.
- `sources` và kết quả tool là dữ liệu cần kiểm tra/escape trước khi hiển thị ra UI.

## 5. Chính sách thay đổi

Được phép: bổ sung implementation mới phía sau Protocol hoặc bổ sung trường response
theo cách backend cũ vẫn đọc được. Không được phép trong v1: đổi tên/xóa tham số,
biến `tenant_id` thành tùy chọn, đổi kiểu trả về, hoặc xóa trường response. Mọi thay
đổi phá vỡ tương thích phải có version mới và contract test mới.
