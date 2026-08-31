# H3-14 — Website không có sitemap hoặc render bằng JavaScript

## Kết quả

- Mẫu được chọn **trước khi crawl** từ pool 12 URL bằng seed `314`; URL lỗi không bị thay.
- Tiêu chí thành công: lấy được ít nhất 1 trang công khai **và** tạo được ít nhất 1 chunk.
- Kết quả ngày 31/08/2026: **9/10 website — 90%**, đạt định nghĩa hoàn thành `>= 8/10`.

| # | Website | Cách khám phá | Trang | Chunk | Kết quả |
|---:|---|---|---:|---:|---|
| 1 | hannguhaiyan.edu.vn | sitemap | 3 | 454 | PASS |
| 2 | flask.palletsprojects.com | sitemap | 1 | 9 | PASS |
| 3 | phongkhamhyhy.com | sitemap | 3 | 28 | PASS |
| 4 | nhabanphuocthinh.com | sitemap | 3 | 5 | PASS |
| 5 | mimadigi.com | sitemap | 3 | 10 | PASS |
| 6 | python.org | link trang chủ | 1 | 8 | PASS |
| 7 | www.djangoproject.com | sitemap | 3 | 40 | PASS |
| 8 | sqlite.org | link trang chủ | 0 | 0 | FAIL |
| 9 | iana.org | link trang chủ | 3 | 9 | PASS |
| 10 | w3.org | link trang chủ | 3 | 10 | PASS |

SQLite có 36 URL cùng miền từ trang chủ nhưng ba trang đầu có quá ít text theo bộ parser. Headless được đề nghị đúng sau khi HTTP thường thiếu text, nhưng máy benchmark chưa cài Playwright/Chromium; lỗi này được lưu trong manifest thay vì âm thầm coi là thành công.

## Flow đã thêm vào pipeline

`crawl_chunks.py` dùng thứ tự cố định:

1. Đọc và tuân thủ `robots.txt`. `404` nghĩa là website không công bố rule cấm; lỗi mạng/HTTP khác thì dừng an toàn.
2. Thử `sitemap.xml` trước.
3. Nếu sitemap thiếu/trống/lỗi, tải trang chủ bằng HTTP và lấy link HTML cùng miền.
4. Loại link file tĩnh, link ngoài miền, admin/cart/checkout/logout và URL có query bất thường.
5. Nếu HTML thường không có link hoặc trang có quá ít text, mới thử Playwright headless.
6. Giới hạn headless mặc định 3 trang, vẫn kiểm tra robots và redirect sau render.
7. Manifest ghi chiến lược, lỗi sitemap, số lần headless thử/thành công, trang bỏ qua và nguồn đã crawl.

Headless là dependency tùy chọn vì tốn RAM/CPU:

```powershell
python -m pip install playwright
playwright install chromium
```

Không cài headless thì crawler vẫn chạy sitemap và homepage-link; khi thực sự cần JS, manifest ghi `HeadlessUnavailable` rõ ràng.

## Cách chạy lại

Benchmark 10 website, tối đa 3 trang/site:

```powershell
python scripts\run_h3_14_benchmark.py
```

Crawl/onboard tenant mới vẫn dùng lệnh H3-03 cũ; fallback H3-14 được bật mặc định trong `crawl_chunks.py`:

```powershell
python scripts\onboard_tenant.py --url "https://website-khach.com" --industry construction
```

Có thể tắt headless khi máy nhẹ:

```powershell
python crawl_chunks.py --base-url "https://website-khach.com" --tenant-id website_khach_com --output outputs/chunks.json --manifest outputs/manifest.json --min-chunks 1 --no-headless-fallback
```

## Bẫy đã khóa

- Không bật headless cho mọi trang; chỉ fallback sau HTTP thường thất bại/thiếu dữ liệu.
- Không vượt `robots.txt`, không theo link ngoài miền và không submit form.
- Không xem HTTP 200 là PASS nếu không sinh được chunk.
- Không đổi URL mẫu sau khi thấy kết quả.
- Không nuốt lỗi JS/dependency; lỗi được ghi vào `discovery_errors` để onboarding biết cần cài Chromium hay xử lý riêng.

Bằng chứng máy đọc được nằm tại `outputs/h3_14/benchmark_results.json` và các manifest theo từng website.
