# Hợp đồng API — ai\_core ↔ Backend



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



## 

