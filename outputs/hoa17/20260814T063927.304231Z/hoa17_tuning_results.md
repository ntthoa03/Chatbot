# HOA-17 — kết quả tinh chỉnh

Mỗi dòng chỉ thay đúng một biến so với `baseline`.

| Cấu hình | Biến thay đổi | Giá trị | Đúng | Δ đúng | Chi phí TB (VND) | Độ trễ TB (ms) | <5 phút | Lỗi |
|---|---|---|---:|---:|---:|---:|:---:|---:|
| baseline | none | production configuration | 70.0% | +0.0% | 11.86 | 1668.97 | Có | 0 |
| chunk_350 | chunk_size | max 350 chars, overlap 60 | 66.7% | -3.3% | 12.53 | 1692.13 | Có | 0 |
| top_k_3 | top_k | 3 | 60.0% | -10.0% | 10.68 | 1225.00 | Có | 0 |
| threshold_070 | min_score | 0.70 | 66.7% | -3.3% | 9.53 | 1162.60 | Có | 0 |
| prompt_clear_refusal | prompt | explicit refusal experiment | 60.0% | -10.0% | 12.30 | 1319.90 | Có | 0 |
| model_gpt_primary | primary_model | gpt-5.6-luna | 65.5% | -4.5% | 7.51 | 2180.50 | Có | 0 |

## Cấu hình được chọn

`baseline` — tỷ lệ đúng 70.0%, chi phí trung bình 11.86 VND/lượt, độ trễ trung bình 1668.97 ms/lượt.

Tiêu chí chọn: không có lỗi hạ tầng, ưu tiên tỷ lệ đúng; nếu bằng nhau thì ưu tiên chi phí rồi độ trễ thấp hơn.
