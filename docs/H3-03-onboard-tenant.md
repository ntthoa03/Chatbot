# H3-03 — Onboard tenant tự động bằng một lệnh

## Lệnh dùng chính

Chỉ cần URL public và mã ngành:

```powershell
python scripts/onboard_tenant.py --url "https://website-khach.com" --industry construction
```

Mã ngành trọng tâm H3-02:

- `construction`, `xay_dung` hoặc `xây dựng`.
- `commerce`, `thuong_mai` hoặc `thương mại`.
- `services`, `dich_vu` hoặc `dịch vụ`.

Hai template bổ trợ vẫn dùng được: `medical`/`y_te` và `retail`/`ban_le`.

`tenant_id` và tên bot được sinh từ hostname. Có thể ghi đè khi cần:

```powershell
python scripts/onboard_tenant.py --url "https://website-khach.com" --industry services --tenant-id ma_tenant --bot-name "Trợ lý Công ty"
```

## Pipeline

Script điều phối, không viết lại logic đã có:

1. `config`: sinh `tenants/<tenant_id>.yaml` chỉ chứa phần riêng và validate bằng template H3-02.
2. `crawl`: gọi `crawl_chunks.py`; mặc định 900 ký tự/chunk, overlap 120, tối đa 50 trang.
3. `index`: gọi `index_chunks.py`; dùng embedding policy của tenant, cache và fallback provider có sẵn.
4. `smoke`: tự tạo đúng 10 câu, gọi `eval/smoke_tenant_index.py`, kiểm tra kết quả RAG và probe sai tenant.

Artifact nằm tại `outputs/h3_03/<tenant_id>/`:

- `onboarding_state.json`: checkpoint từng bước và số lần thử.
- `onboarding.log`: lệnh và log đầy đủ của từng bước.
- `chunks.json`, `crawl_manifest.json`.
- `index/`: vector, metadata, manifest và embedding cache.
- `smoke_questions.json`, `smoke_report.json`.
- `summary.json`: kết quả máy đọc, thời gian, provider/model và lệnh resume.

## Lỗi và chạy tiếp

Nếu robots, sitemap, một trang web hoặc API embedding lỗi, script:

- Không in traceback chưa xử lý.
- Ghi bước `failed` cùng thông báo lỗi vào checkpoint.
- Giữ nguyên các bước đã thành công.
- Ghi `summary.json` ngay cả khi thất bại.
- In lệnh tiếp tục.

Chạy lại từ đúng bước lỗi:

```powershell
python scripts/onboard_tenant.py --url "https://website-khach.com" --industry services --tenant-id ma_tenant --resume
```

Chủ động chạy lại từ một bước và toàn bộ bước phụ thuộc phía sau:

```powershell
python scripts/onboard_tenant.py --url "https://website-khach.com" --industry services --tenant-id ma_tenant --from-step crawl
```

Không dùng `--from-step config` nếu chưa chủ ý kiểm tra lại toàn bộ pipeline. Script không tự ghi đè config khác nội dung để tránh phá tenant đã vận hành.

Website không có sitemap hoặc không đọc được robots sẽ dừng có kiểm soát tại `crawl`. H3-03 bảo đảm quan sát lỗi và resume; phương án dò link trang chủ/headless cho site JavaScript thuộc H3-14, không âm thầm crawl trái robots trong task này.

## Acceptance đã chạy

Lệnh đúng hai tham số:

```powershell
python scripts/onboard_tenant.py --url "https://mimadigi.com" --industry services
```

Kết quả tenant `mimadigi_com` ngày 26/08/2026:

| Chỉ số | Kết quả |
|---|---:|
| Thời gian toàn bộ | 108,246 giây |
| Giới hạn | 900 giây |
| Trang crawl | 50 |
| Chunk/index record | 393 |
| Embedding | OpenAI `text-embedding-3-small` sau khi Gemini primary lỗi |
| Smoke | 10/10 câu có kết quả |
| Probe sai tenant | PASS — 0 chunk |

Một lần thử giới hạn 15 trang chỉ tạo 8/10 chunk đã dừng đúng tại `crawl`. Lệnh `--resume` giữ bước config, chạy lại từ crawl với 30 trang rồi hoàn tất 54 chunk, smoke 10/10 và isolation PASS. Đây là kiểm chứng thực tế cho bẫy “site đa dạng hoặc crawl thất bại không được làm script chết giữa chừng”.

## Test tự động

```powershell
python -m unittest tests.test_h3_03 tests.test_h3_02 tests.test_h3_01 tests.test_tenant_isolation -v
```
