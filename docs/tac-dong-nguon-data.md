# H4-12 — Tác động của từng nguồn data

> Phạm vi: chỉ tenant `mima_internal`. Số liệu gồm eval đã chạy và acceptance TEST/SYNTHETIC; chưa phải KPI production.

## Kết quả định lượng

| Hạng | Nguồn | Số câu | Điểm eval trước → sau | Tăng điểm | Đủ dữ liệu trước → sau | Tăng độ phủ | Cách đo |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | tenant tự trả lời | 5 | 0.0% → 100.0% | 100.0% | 20.0% → 100.0% | 80.0% | offline immediate-index acceptance, TEST/SYNTHETIC answers |
| 2 | tài liệu | 12 | 0.0% → 41.7% | 41.7% | 100.0% → 100.0% | 0.0% | existing online model eval, TEST/SYNTHETIC documents |
| 3 | crawl | 12 | 0.0% → 0.0% | 0.0% | 0.0% → 100.0% | 100.0% | existing controlled eval; empty control has no retrievable evidence |
| 4 | log chat | 12 | 0.0% → 0.0% | 0.0% | 8.3% → 83.3% | 75.0% | offline evidence retrieval; no external egress |

## Kiểm tra nền crawl H4-03

Trên 15 case H4-03 của cùng tenant: điểm hiệu dụng **86.7%**, tỷ lệ đủ dữ liệu **86.7%**. Dòng crawl trong bảng dùng bộ 12 case thiếu dữ liệu H4-05 để đo đóng góp so với kho rỗng.

## Kết luận

Trong acceptance hiện có, tenant tự trả lời tạo mức tăng accuracy lớn nhất; tài liệu đứng sau. Đây là thứ hạng thử nghiệm, phải đo lại trên cùng một bộ case production trước quyết định ngân sách quý.

Ưu tiên đề xuất: (1) thu câu trả lời tenant cho gap có tần suất cao; (2) tiếp tục nạp tài liệu có cấu trúc; (3) log chat chỉ đưa nhánh knowledge đã tách khỏi eval vào kho; (4) crawl vẫn là nền độ phủ.

## Giới hạn bắt buộc khi diễn giải

- Mọi nguồn được đo trên cùng tenant, nhưng mỗi A/B dùng bộ case phù hợp nguồn; không cộng các phần trăm thành một chuỗi nhân quả.
- Tài liệu, log chat và câu tenant trong acceptance hiện là TEST/SYNTHETIC; không trình bày các số này như hiệu quả khách hàng thật.
- Eval log chat là đo evidence retrieval offline vì môi trường không cho phép gửi knowledge chunks nội bộ ra API ngoài.
- Trước quyết định ngân sách quý, cần chạy lại cùng một bộ case production cố định và có tenant xác nhận ground truth.

## Truy vết

- Crawl: `outputs/h4_03/report.json`
- Tài liệu: `outputs/h4_05/experiment/h4_05_comparison.json`
- Log chat: `outputs/h4_06/audit.json` + split H4-07
- Tenant: `outputs/h4_11/acceptance.json`
