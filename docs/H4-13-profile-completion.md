# H4-13 — Thanh hoàn thiện hồ sơ

Thành phần được hiển thị ở đầu trang `Duyệt hồ sơ` trong `app.py`. Logic tính nằm tại `ingestion/profile_completion.py`.

## Công thức

```text
% hoàn thiện = tổng tần suất chủ đề H4-02 đã được tenant xác nhận
               / tổng tần suất toàn bộ chủ đề H4-02
```

Mẫu số là lượt hỏi thật trong H4-02, không phải sáu trường cố định của schema hồ sơ. Một chủ đề chỉ hoàn thành khi nội dung câu hỏi ánh xạ được tới mục hồ sơ và quyết định H4-09 mới nhất là `confirmed` hoặc `edited`.

UI luôn hiển thị việc nên làm tiếp theo, số phút ước tính và lợi ích tính trực tiếp từ tần suất. Ví dụ acceptance: bảng giá đã xác nhận giải quyết 8/16 lượt hỏi thiếu nên tiến độ là 50%; câu tiếp theo có tần suất 5/16 nên lợi ích hiển thị khoảng 31%.

Nếu H4-02 không có cluster, UI ghi rõ chưa đủ dữ liệu và không hiển thị phần trăm giả. Output H4-02 hiện tại thuộc trường hợp này; acceptance UI dùng fixture `tests/fixtures/h4_10_clusters_5_tenants.json` có nhãn TEST/SYNTHETIC.

Biến cấu hình tùy chọn:

```dotenv
AI_CORE_GAP_CLUSTER_PATH=outputs/h4_02/knowledge_gap_clusters.json
AI_CORE_PROFILE_REVIEW_ROOT=outputs/h4_09/reviews
```
