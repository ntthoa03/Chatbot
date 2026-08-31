# H3-13 — Báo cáo khả thi tầng tri thức ngành

## Kết luận

- A/B offline trên **15 câu / 5 ngành**.
- Chỉ dữ liệu tenant mới: **4/15 = 26.67%**.
- Tenant + tầng ngành: **15/15 = 100.00%**.
- Mức tăng tuyệt đối: **73.33%**; định nghĩa hoàn thành: **ĐẠT**.
- Chi phí API: **$0.00000000** vì phép thử deterministic không gọi LLM.

## Theo ngành

| Ngành | Baseline | Có tầng ngành |
|---|---:|---:|
| `digital_agency` | 1/3 | 3/3 |
| `education` | 1/3 | 3/3 |
| `food` | 1/3 | 3/3 |
| `medical_clinic` | 0/3 | 3/3 |
| `real_estate` | 1/3 | 3/3 |

## Kiểm soát bẫy

- Năm YAML đã qua validator chặn danh tính tenant, URL, email, số điện thoại và giá cụ thể.
- Tầng ngành nằm ngoài `tenants/` và không được gọi tự động từ runtime.
- Không có kết luận rằng đây là chuẩn ngành hoặc chất lượng production.

## Giới hạn

- Chỉ có một tenant quan sát cho mỗi ngành; không đủ để suy rộng mẫu hình.
- A/B đo độ phủ deterministic trên dữ liệu synthetic, không đo chất lượng sinh của LLM.
- Chưa nối tầng ngành vào runtime; năm bot hiện tại không bị thay đổi.
- Production cần data owner duyệt, log khách thật, guardrail và isolation regression.

## Quyết định

Kết quả đủ chứng minh hướng tiếp cận có thể tăng độ phủ cho tenant mới có dữ liệu mỏng. Chỉ nên tiếp tục pilot sau khi có thêm tenant cùng ngành và dữ liệu thật đã được ẩn danh, duyệt nghiệp vụ.
