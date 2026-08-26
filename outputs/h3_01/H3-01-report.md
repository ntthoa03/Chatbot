# H3-01 — Mở rộng hệ thống lên 5 tenant/5 ngành

Ngày thực hiện: 2026-08-25  
Phạm vi: dùng lại `crawl_chunks.py`, config loader, `index_chunks.py` và local retriever đã có từ tuần 2; **không sửa `ai_core`**.

Danh mục máy đọc liệt kê đủ đường dẫn 5 index và từng file dữ liệu nằm tại `outputs/h3_01/index_catalog.json`.

## 1. Kết quả

| Thứ tự | Tenant | Ngành | Config | Index | Chunk | Smoke RAG | Cách ly |
|---:|---|---|---|---|---:|---|---|
| 1 | `mima_internal` | Digital agency | `tenants/mima_internal.yaml` | `index/` | 40 | 3/3 có kết quả | PASS |
| 2 | `phongkham_hyhy` | Y tế | `tenants/phongkham_hyhy.yaml` | `outputs/h2_04/index_phongkham_hyhy/` | 776 | 3/3 có kết quả | PASS |
| 3 | `bat_dong_san_phuoc_thinh` | Bất động sản | `tenants/bat_dong_san_phuoc_thinh.yaml` | `outputs/h3_01/index_bat_dong_san_phuoc_thinh/` | 52 | 3/3 có kết quả | PASS |
| 4 | `giao_duc_haiyan` | Giáo dục | `tenants/giao_duc_haiyan.yaml` | `outputs/h3_01/index_giao_duc_haiyan/` | 1.369 | 3/3 có kết quả | PASS |
| 5 | `thuc_pham_thien_minh` | Thực phẩm | `tenants/thuc_pham_thien_minh.yaml` | `outputs/h3_01/index_thuc_pham_thien_minh/` | 41 | 3/3 có kết quả | PASS |

Ba website mới là website công khai thuộc danh mục dự án khách MIMA:

- Phước Thịnh: `https://www.nhabanphuocthinh.com/`
- Trung tâm Tiếng Trung HaiYan: `https://hannguhaiyan.edu.vn/`
- Thực Dưỡng Thiện Minh: `https://thucphamchaythienminh.com/`

Năm ngành không trùng nhau: digital agency, y tế, bất động sản, giáo dục, thực phẩm.

## 2. Thời gian onboarding thực tế

Đơn vị: giây. `Tổng kỹ thuật` = các lần crawl (kể cả lần thất bại) + validate config + tạo index + smoke test. Không cộng thời gian chọn website/duyệt nghiệp vụ thủ công; hai tenant cũ không có log thời gian từ tuần trước nên để trống thay vì ước lượng.

| Tenant mới | Crawl lỗi | Crawl đạt | Validate config | Index | Smoke | Tổng kỹ thuật |
|---|---:|---:|---:|---:|---:|---:|
| Tenant 3 — BĐS Phước Thịnh | 47,436 | 44,821 | 0,305 | 8,277 | 4,644 | **105,483** |
| Tenant 4 — Giáo dục HaiYan | 0 | 143,252 | 0,267 | 24,994 | 4,201 | **172,714** |
| Tenant 5 — Thực phẩm Thiện Minh | 0 | 77,600 | 0,429 | 4,596 | 4,897 | **87,522** |

Tenant 5 nhanh hơn tenant 3 **17,961 giây (17,0%)**, đạt tiêu chí cứng của H3-01. Chi tiết dạng máy đọc nằm trong `outputs/h3_01/onboarding_times.csv`.

## 3. Cấu hình và cách chạy lại

Thông số crawl giữ theo tuần 2: chunk size `900`, overlap `120`, min chars `160`. Tenant 5 giới hạn `50` trang sau khi tenant 4 cho thấy crawl toàn bộ 100 trang là bottleneck; ngưỡng H3-01 dùng `40` chunk vì task không yêu cầu tối thiểu 100 chunk như H2-04.

Ví dụ tenant bất động sản:

```powershell
python crawl_chunks.py --base-url https://www.nhabanphuocthinh.com/ --tenant-id bat_dong_san_phuoc_thinh --output outputs/h3_01/bat_dong_san_phuoc_thinh_chunks.json --manifest outputs/h3_01/bat_dong_san_phuoc_thinh_crawl_manifest.json --max-pages 100 --min-chunks 40 --chunk-size 900 --chunk-overlap 120 --min-chars 160
python index_chunks.py --tenant-id bat_dong_san_phuoc_thinh --input outputs/h3_01/bat_dong_san_phuoc_thinh_chunks.json --out-dir outputs/h3_01/index_bat_dong_san_phuoc_thinh
python eval/smoke_tenant_index.py --index-dir outputs/h3_01/index_bat_dong_san_phuoc_thinh --tenant-id bat_dong_san_phuoc_thinh --wrong-tenant mima_internal --output outputs/h3_01/smoke_bat_dong_san_phuoc_thinh.json --query "Có nhà nào bán ở Quận 8?"
```

Kiểm tra toàn bộ bằng một lệnh:

```powershell
python -m unittest tests.test_h3_01 -v
```

## 4. Bằng chứng và điểm cần theo dõi

- Mỗi index có `manifest.json`, `metadata.json`, `vectors.npy` và cache embedding riêng.
- Metadata của từng index chỉ chứa đúng `tenant_id` sở hữu; probe bằng tenant MIMA trên 4 index khác và ngược lại đều trả 0 chunk.
- Index giáo dục có 1.369 chunk; Gemini embedding thất bại trong lần chạy, fallback `openai/text-embedding-3-small` được dùng thành công đúng chính sách config.
- Truy vấn liên hệ của tenant giáo dục có kết quả nhưng top-1 chưa tối ưu do nhiều bài viết lặp nội dung. Đây là vấn đề chất lượng/ranking cần xử lý ở bước deduplicate/eval sau, không phải lỗi multi-tenant.
- Tenant 3 có một trang crawl lỗi; manifest giữ nguyên chi tiết để review, không làm thất bại toàn bộ 52 chunk hợp lệ.

## 5. Đối chiếu Definition of Done H3-01

| Tiêu chí | Kết quả |
|---|---|
| Có 5 config tenant | Đạt: 5 file config thật (không tính template) |
| Có 5 index | Đạt: 5 manifest/index riêng |
| Có 5 ngành thực sự khác nhau | Đạt |
| Cả 5 tenant chạy không sửa code | Đạt; chỉ thêm config và artifacts dữ liệu/index |
| Ghi thời gian từng tenant mới | Đạt; đo từng bước, có CSV |
| Tenant 5 nhanh hơn tenant 3 | Đạt: 87,522s < 105,483s |
