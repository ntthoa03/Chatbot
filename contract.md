# Hợp đồng API — ai_core ↔ Backend

## 1\. Hợp đồng `chat(payload)`

### REQUEST

```json
{
  "tenant\_id": "mima\_internal",
  "conversation\_id": "uuid",
  "message": "Làm web bao nhiêu tiền em?",
  "history": \[
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "config\_version": 1
}
```

### RESPONSE

```json
{
  "reply": "...",
  "sources": \[
    { "chunk\_id": "...", "url": "...", "score": 0.82 }
  ],
  "tool\_calls": \[
    { "name": "check\_domain", "args": { }, "result": { } }
  ],
  "need\_human": false,
  "lead\_captured": { "name": "...", "phone": "..." },
  "guardrail": { "blocked": false, "reason": null },
  "usage": { "model": "...", "tokens\_in": 1240, "tokens\_out": 180, "cost\_vnd": 42, "latency\_ms": 1850 },
  "trace\_id": "uuid"
}
```

`lead\_captured` là `null` khi chưa thu được lead trong lượt trả lời này.

### STREAMING

`chat(payload, stream=True)` trả về iterator sự kiện. Nội dung chỉ được phát sau khi
đã qua output guardrail:

```json
{ "type": "delta", "delta": "một phần câu trả lời", "trace_id": "uuid" }
```

Sự kiện cuối luôn chứa nguyên response đúng cấu trúc ở trên, bao gồm cả `sources`:

```json
{
  "type": "done",
  "response": {
    "reply": "...", "sources": [], "tool_calls": [], "need_human": false,
    "lead_captured": null, "guardrail": {"blocked": false, "reason": null},
    "usage": {"model": "...", "tokens_in": 0, "tokens_out": 0, "cost_vnd": 0, "latency_ms": 0},
    "trace_id": "uuid"
  },
  "trace_id": "uuid"
}
```

\---

## 2\. Hợp đồng chunk tri thức

```json
{
  "tenant\_id": "mima\_internal",
  "chunk\_id": "uuid",
  "content": "...",
  "metadata": {
    "url": "https://mimadigi.com/...",
    "title": "...",
    "type": "service|pricing|policy|faq|blog",
    "updated\_at": "2026-08-07"
  }
}
```

`seed\_chunks.json` (đầu vào của HOA-05) là 1 mảng JSON gồm nhiều object
đúng cấu trúc trên.

Đây là **cấu trúc nghiệp vụ chuẩn** và được validate bởi `KnowledgeChunk`.
Khi ghi vào vector database, cấu trúc được ánh xạ sang envelope có thể filter:

```json
{
  "id": "uuid (= chunk_id)",
  "namespace": "mima_internal (= tenant_id)",
  "values": ["embedding vector"],
  "metadata": {
    "tenant_id": "mima_internal",
    "chunk_id": "uuid",
    "content": "...",
    "url": "https://mimadigi.com/...",
    "title": "...",
    "type": "service|pricing|policy|faq|blog",
    "updated_at": "2026-08-07"
  }
}
```

`tenant_id` được dùng đồng thời làm namespace và metadata filter để cách ly tenant.
Việc làm phẳng metadata chỉ xảy ra trong envelope của vector database; dữ liệu trao
đổi giữa Hiếu và Hoa vẫn giữ nguyên object `metadata` lồng như hợp đồng ở trên.

##
