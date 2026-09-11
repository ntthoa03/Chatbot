# H4-08 — Sinh bản nháp hồ sơ doanh nghiệp từ crawl

## Schema output

Mỗi `BusinessProfileDraft` có trạng thái `draft_unconfirmed` và đúng sáu trường:

- `industry` — ngành nghề;
- `main_services` — dịch vụ chính;
- `operating_regions` — khu vực hoạt động;
- `target_customers` — tệp khách mục tiêu;
- `brand_tone` — giọng điệu thương hiệu;
- `public_pricing` — bảng giá có số công khai trên website.

Mỗi trường luôn có `value`, `confidence`, `citations`, `note`. Giá trị có dữ
liệu chỉ được giữ khi có confidence hợp lệ và ít nhất một citation đã xác minh:
`source_id`, `chunk_id`, URL, tiêu đề trang và đoạn trích nguyên văn. Nếu model
dẫn sai nguồn/quote hoặc không đủ bằng chứng, code hạ trường về `value=null`,
`confidence=0`, `citations=[]` và ghi lý do; không tự bù bằng config hay kiến
thức nền.

## Luồng xử lý

```text
index_catalog (5 tenant)
  -> tải crawl chunks và từ chối file lẫn tenant
  -> bỏ nội dung trùng, chọn tối đa 45 chunk đa dạng URL
  -> LLM temperature=0, tool bị tắt, SOURCE được coi là untrusted
  -> parse schema rút gọn
  -> xác minh quote nằm nguyên văn trong đúng chunk
  -> grounding judge độc lập kiểm tra value có thật sự được quote chứng minh
  -> số tiền/đơn vị số phải xuất hiện trong citation, sai thì xóa cả trường giá
  -> BusinessProfileDraft chờ tenant xác nhận
```

Việc giới hạn 45 chunk giúp lượt sinh nháp nằm trong context/cost kiểm soát;
`source_chunk_count` và `selected_source_count` được ghi rõ để không tạo cảm
giác đã đọc toàn bộ khi chỉ dùng mẫu đại diện.

## Chạy và kết quả hiện tại

```powershell
python -m ingestion.profile_generator `
  --catalog outputs\h3_01\index_catalog.json `
  --output-dir outputs\h4_08 `
  --max-sources 45 --max-chars 45000
```

Đây cũng là lệnh chạy lại khi thay bằng catalog crawl thật. Pipeline preflight
toàn bộ file/tenant trước khi gọi model, dùng primary/fallback đã cấu hình và
ghi JSON bằng temporary file + `fsync` + atomic replace. Không dùng
`--skip-grounding-judge` ngoài unit test/offline vì output khi đó chưa đủ chuẩn
production.

Mỗi tenant được lưu tuyệt đối riêng tại
`outputs/h4_08/tenants/{tenant_id}/business_profile.draft.json`. File
`profiles_manifest.json` chỉ chứa tenant, đường dẫn và SHA-256; không chứa nội
dung hồ sơ của nhiều tenant. UI H4-09 luôn dựng đường dẫn từ tenant đã validate.

Đã chạy trên 5 tenant, tạo đủ 6 trường/tenant: 30 trường tổng cộng, 18 trường
có giá trị và citation hợp lệ, 12 trường để null có ghi chú, 0 trường có giá
trị nhưng thiếu nguồn. Tổng chi phí model ghi nhận là 0,0230392 USD.

Nguồn MIMA là `TEST/SEED`; bốn tenant còn lại dùng crawl snapshot đã có trong
repo. Tất cả vẫn là bản nháp chưa được tenant xác nhận và không được tự động
dùng như dữ liệu chuẩn.
