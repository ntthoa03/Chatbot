# Ai_core — Chatbot (Tuần 1)

## Chạy thử

```bash
python3 -m ai_core.chat
```

Cấu trúc package

```
ai_core/
├── __init__.py
├── chat.py               # Cửa vào duy nhất: chat(payload) -> response
│                          # (02 dựng khung, ghép các module bên dưới)
├── models.py              # Pydantic model — hiện thực hoá hợp đồng contract.md
│                          # (01)
├── config.py               # STUB — load_config(tenant_id, config_version)
│                          # 04 điền thật: đọc tenants/*.yaml
├── retriever.py             # STUB — retrieve(query, tenant_id, k)
│                          # 06 điền RAG thật (dựa trên index của 05)
├── prompt.py                # STUB — build_system_prompt(config)
│                          # 07 sinh prompt từ config thật
├── router.py                  # STUB — decide_need_human(message)
│                          # 14 điền logic chuyển người thật
├── trace.py                    # STUB — new_trace_id(), log_trace(record)
│                          # 16 ghi trace đầy đủ ra file
├── evaluator.py                  # STUB — run_eval(cases_path)
│                          # 13 điền khung eval
├── guardrail/
│   ├── __init__.py
│   ├── input.py                   # STUB — check_input(message)
│   │                          # 11 chặn injection/spam
│   └── output.py                    # STUB — check_output(reply)
│                          # 12 chặn cam kết sai, rủi ro cao nhất
└── tools/
    ├── __init__.py
    └── registry.py                     # STUB — đăng ký tool check_domain
                          # 10 điền thật (mock -> thật khi Hiếu xong)
```
