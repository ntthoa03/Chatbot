# H3-09 — So sánh chất lượng 5 tenant

> Điểm được chấm trên 15 câu riêng/tenant, cùng evaluator và cùng tiêu chí keyword; không lấy điểm MIMA đại diện cho ngành khác.

| Tenant | Ngành | Đạt | Tỷ lệ đúng | Lỗi | Chi phí TB | Độ trễ TB | Nhóm yếu nhất |
|---|---|---:|---:|---:|---:|---:|---|
| `bat_dong_san_phuoc_thinh` | real_estate | 12/15 | 80.0% toàn bộ / 80.0% đã chấm | 0 | $0.00172171 | 4774 ms | services (66.7%) |
| `mima_internal` | digital_agency | 13/15 | 86.7% toàn bộ / 86.7% đã chấm | 0 | $0.00082354 | 3766 ms | services (80.0%) |
| `thuc_pham_thien_minh` | food | 13/15 | 86.7% toàn bộ / 92.9% đã chấm | 1 | $0.00153915 | 4300 ms | snacks (66.7%) |
| `phongkham_hyhy` | medical_clinic | 14/15 | 93.3% toàn bộ / 100.0% đã chấm | 1 | $0.00128660 | 3371 ms | Không có (100.0%) |
| `giao_duc_haiyan` | education | 15/15 | 100.0% toàn bộ / 100.0% đã chấm | 0 | $0.00160008 | 4219 ms | Không có (100.0%) |

## Tenant yếu nhất

- **bat_dong_san_phuoc_thinh — 80.0%.**
- Có 3 câu sai; nhóm yếu nhất services (66.7%), nguyên nhân chính: blocked_output.

## Cách đọc kết quả

- `ERROR` là lỗi hạ tầng/provider, không được nhập chung thành lỗi kiến thức.
- `FAIL` là câu trả lời không đạt keyword/ràng buộc tenant của case.
- Xem `details.csv` để đọc nguyên câu hỏi, reply và tiêu chí sai; xem `topics.csv` để so theo chủ đề.

## Bẫy đã tránh

- Mỗi tenant dùng câu hỏi lấy từ index của chính website đó.
- Mỗi case đều cấm xuất hiện tên tenant khác để kiểm tra rò chéo ở mức câu trả lời.
- Kết luận tenant yếu nhất dựa trên điểm riêng và chủ đề yếu, không suy rộng từ MIMA.
