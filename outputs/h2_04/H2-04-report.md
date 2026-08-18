# H2-04 — Crawl và dựng tri thức tenant thứ hai

Ngày thực hiện: 2026-08-18  
Website: https://phongkhamhyhy.com  
Tenant ID: `phongkham_hyhy`

## 1. Yêu cầu gốc đã đối chiếu

Theo dòng H2-04 trong `Task-Tuan-2-HOA.xlsx`:

- Chọn khách hàng khác ngành với MIMA: `phongkhamhyhy.com` (y tế).
- Dùng pipeline crawl/chunk bằng tham số và sinh index riêng.
- Deliverable: index riêng tenant thứ hai, tối thiểu 100 chunks.
- Trap: nếu phải sửa code, ghi đúng vị trí vì có thể đang hardcode cho MIMA.

H2-05 (config/persona/bot cho tenant mới) là task kế tiếp, không được trộn vào H2-04.

## 2. Kết quả

| Tiêu chí | Kết quả | Trạng thái |
|---|---:|---|
| URL tìm thấy trong sitemap | 239 | Đạt |
| URL được robots.txt cho phép | 239 | Đạt |
| Trang tải trong lần crawl | 100 | Đạt |
| Lỗi tải trang | 0 | Đạt |
| Chunks trước loại trùng | 781 | Thông tin |
| Chunks trùng nội dung đã loại | 5 | Đạt |
| Chunks cuối | **776** | **Đạt yêu cầu >= 100** |
| Tenant đúng `phongkham_hyhy` | 776/776 | Đạt |
| Hash nội dung trùng còn lại | 0 | Đạt |
| URL thực sự đóng góp chunks | 97 | Đạt |
| Độ dài chunk min / TB / max | 197 / 766,4 / 1.015 ký tự | Đạt |
| Loại metadata | 413 `service`, 363 `blog` | Đạt schema |
| Index record | **776** | Đạt |
| Embedding | OpenAI `text-embedding-3-small`, 1.536 chiều | Đạt |
| Tenant trong index manifest | chỉ `phongkham_hyhy` | Đạt |
| Probe bằng tenant MIMA | 0 kết quả | **Cách ly đạt** |
| Test toàn repo | **171/171 pass** | Đạt |

Các file bàn giao:

- `phongkham_hyhy_chunks.json`: 776 chunks đúng schema `KnowledgeChunk`.
- `crawl_manifest.json`: nguồn, thời điểm, robots/sitemap, lỗi và URL đã crawl.
- `index_phongkham_hyhy/`: `vectors.npy`, `metadata.json`, `manifest.json`, cache embedding.
- `index_smoke.json`: 5 truy vấn semantic và probe cách ly tenant.

## 3. Kiểm thử truy vấn thật

| Câu hỏi | Top source | Điểm top | Nhận xét |
|---|---|---:|---|
| Gói khám đánh giá nguy cơ đột quỵ gồm những gì? | Khám Đột Quỵ Công Nghệ Cao Không Đau | 0,541005 | Có 3 nguồn liên quan; trang đúng tên gói ở vị trí 3 |
| Phòng khám có khám tim mạch không? | Phòng Khám Đa Khoa Khám Tim Mạch Uy Tín | 0,643564 | Đúng chủ đề |
| Địa chỉ và giờ làm việc của phòng khám | Phòng Khám Đa Khoa Khám Ngoài Giờ Tiện Lợi | 0,584126 | Đúng chủ đề |
| Bác sĩ Hồ Hữu Thật chuyên khoa gì? | Hồ Hữu Thật — bác sĩ điều trị đột quỵ | 0,615600 | Đúng bác sĩ/chuyên môn |
| Xét nghiệm HbA1c là gì? | Cử nhân xét nghiệm Thiều Thị Bích Ngọc | 0,534907 | Có nguồn xét nghiệm liên quan nhưng bộ 100 trang đầu chưa chứa bài HbA1c chuyên biệt |

Kết quả trên là smoke test retrieval, chưa phải đánh giá an toàn nội dung y tế của chatbot. H2-05 phải dùng đúng embedding `openai/text-embedding-3-small` hoặc dựng lại index bằng model khác; query model khác manifest sẽ bị từ chối.

## 4. Chỗ phải sửa code và hardcode MIMA

Tiêu chí “crawl xong không sửa pipeline” **không đạt ở trạng thái repo ban đầu**, và đã được ghi nhận trung thực:

1. Repo ban đầu không có file/pipeline crawl nào; chỉ có `index_chunks.py` nhận JSON có sẵn. Đã thêm pipeline generic `crawl_chunks.py`:
   - Dòng 297–397: crawl theo `base_url`/`tenant_id`, kiểm tra robots, chỉ cùng miền, loại redirect/hash trùng.
   - Dòng 400–424: URL, tenant, số trang, chunk size, overlap và ngưỡng đều là tham số CLI.
   - Không submit form; bỏ `form`, `input`, `script`, `style`, dữ liệu chỉ lấy từ trang công khai.
2. `index_chunks.py` ban đầu gửi toàn bộ chunks trong một request embedding. Với 776 chunks, Gemini trả `400 INVALID_ARGUMENT: at most 100 requests can be in one batch`.
   - Đã sửa dòng 134–175: thêm `batch_size`, chia request theo batch.
   - Đã sửa dòng 266–281: truyền batch size xuyên qua fallback.
   - Đã thêm CLI `--batch-size` tại dòng 299–304 và truyền tại dòng 320.
   - Test 205 chunks xác nhận chia đúng `[100, 100, 5]`.
3. Hai mặc định đang gắn với dữ liệu MIMA nhưng **không cần sửa để chạy tenant mới**:
   - `index_chunks.py:293`: `--input` mặc định là `seed_chunks.json` (file MIMA).
   - `index_chunks.py:296`: `--tenant-id` mặc định là `mima_internal`.
   - Lần H2-04 đã luôn truyền rõ `--input ...phongkham_hyhy_chunks.json` và `--tenant-id phongkham_hyhy`; không có dữ liệu MIMA trong output/index.
4. Đã thêm `eval/smoke_tenant_index.py` để chạy truy vấn có nguồn và test tenant sai trả 0.
5. Đã thêm `tests/test_h2_04.py`; không sửa config/persona MIMA và không tạo config tenant H2-05.
6. Lần crawl thành công ban đầu dùng nhãn User-Agent `MIMA-H2-04-KnowledgeCrawler/1.0`, vì vậy manifest giữ nguyên dấu vết chạy thật này. Sau kiểm tra hardcode, mã hiện tại tại `crawl_chunks.py:39` đã đổi thành nhãn generic `TenantKnowledgeCrawler/1.0`; thay đổi chỉ ảnh hưởng nhãn HTTP khi chạy lại, không sửa dữ liệu đã crawl.

Phân loại nguyên nhân: điểm (3) là hardcode mặc định MIMA thực sự. Điểm (1) là pipeline crawl bị thiếu; điểm (2) là giới hạn batch provider, không phải hardcode MIMA nhưng chỉ lộ ra khi dữ liệu tenant mới lớn hơn.

## 5. Sự cố và quyết định provider

- Gemini lần đầu từ chối vì batch 776 > 100; pipeline đã được sửa.
- Sau khi chia batch, Gemini trả `429 RESOURCE_EXHAUSTED` do free-tier giới hạn 100 embedding requests/phút.
- Chuyển bằng tham số có sẵn sang OpenAI `text-embedding-3-small`; index hoàn tất 776/776.
- Chạy lại cùng lệnh: `0 embed mới, 776 lấy từ cache`, chứng minh không tốn lại API khi nội dung không đổi.

## 6. Lệnh tái tạo

```powershell
python crawl_chunks.py --base-url https://phongkhamhyhy.com --tenant-id phongkham_hyhy --output outputs/h2_04/phongkham_hyhy_chunks.json --manifest outputs/h2_04/crawl_manifest.json --max-pages 100 --min-chunks 100 --chunk-size 900 --chunk-overlap 120 --min-chars 160 --delay-seconds 0.15

python index_chunks.py --input outputs/h2_04/phongkham_hyhy_chunks.json --out-dir outputs/h2_04/index_phongkham_hyhy --tenant-id phongkham_hyhy --provider openai --model text-embedding-3-small --batch-size 100

python eval/smoke_tenant_index.py --index-dir outputs/h2_04/index_phongkham_hyhy --tenant-id phongkham_hyhy --wrong-tenant mima_internal --output outputs/h2_04/index_smoke.json --threshold 0.30 --top-k 3
```

## 7. Giới hạn cần nhớ cho y tế

- Đây là bản sao tri thức từ website công khai, không phải kiểm định y khoa độc lập.
- Không dùng chunks để tự chẩn đoán, kê đơn hoặc thay thế bác sĩ.
- H2-05 cần cấu hình persona/guardrail y tế, cảnh báo cấp cứu và chuyển người thật; H2-04 chỉ hoàn thành crawl/chunk/index và cách ly tenant.
