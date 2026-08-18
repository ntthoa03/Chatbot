# H2-01 — bộ eval 60 câu normal

## Phạm vi

`eval/cases.yaml` hiện có 60 case `type=normal` và giữ nguyên 14 trap của tuần 1. Phân bổ normal:

| Chủ đề | Số case |
|---|---:|
| Thiết kế website (`website_design`) | 20 |
| SEO (`seo`) | 10 |
| Tên miền (`domain`) | 8 |
| Hosting/email (`hosting_email`) | 7 |
| Quảng cáo (`ads`) | 8 |
| Quy trình/hợp đồng (`process_contract`) | 7 |
| **Tổng** | **60** |

Mọi normal case đều khai báo đồng thời `must_contain` và `must_not_contain`. Câu hỏi gồm cách viết có dấu, không dấu, viết tắt, sai chính tả nhẹ và câu thiếu ngữ cảnh thường gặp trong chat.

## Nguồn tạm

Do chưa có bản xuất Zalo/Fanpage thật, nguồn hiện tại là `eval/synthetic_zalo_fanpage_h2_01.jsonl`. Tất cả bản ghi đều có `synthetic=true` và ánh xạ một-một tới normal case bằng `case_id`. Không được trình bày nguồn này như dữ liệu khách hàng thật.

Khi log thật về, thay dần câu tổng hợp bằng nguyên văn đã ẩn thông tin cá nhân; giữ lại topic và tiêu chí chấm dựa trên kho tri thức đã xác minh.

## Báo cáo

Evaluator ghi `topic` vào JSON/CSV chi tiết và sinh các metric sau cho từng nhóm trong summary:

- tỷ lệ đúng;
- số đạt/tổng số;
- chi phí trung bình;
- độ trễ trung bình;
- số lỗi hạ tầng và số case chờ review.

Chạy kiểm tra offline:

```powershell
python -m unittest tests.test_h2_01 -v
```

Chạy eval thật:

```powershell
python -m eval.run
```
