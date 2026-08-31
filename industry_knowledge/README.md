# H3-13 — Tầng tri thức ngành thử nghiệm

Thư mục này tách hoàn toàn khỏi `tenants/` và vector index của từng khách. Mỗi
YAML chỉ chứa thuật ngữ, câu hỏi phổ biến, hướng dẫn trả lời và điều không được
cam kết ở mức ngành.

Nguyên tắc bắt buộc:

- Không có `tenant_id`, thương hiệu, URL, địa chỉ, email, số điện thoại hay giá.
- Luôn ghi `experimental: true`, số tenant quan sát và giới hạn bằng chứng.
- Không được tự động ghép vào runtime. Muốn production phải qua data owner,
  guardrail review, eval A/B trên log thật và kiểm tra isolation.
- Tri thức tenant vẫn có độ ưu tiên cao hơn; tầng ngành chỉ được dùng khi câu hỏi
  là kiến thức chung và không được dùng để trả dữ liệu thay đổi hoặc thông tin riêng.

Chạy thử nghiệm khả thi:

```powershell
python scripts/evaluate_h3_13_industry_knowledge.py
python -m unittest tests.test_h3_13 -v
```
