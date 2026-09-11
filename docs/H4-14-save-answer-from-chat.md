# H4-14 — Lưu câu trả lời ngay trong chat

Mỗi câu trả lời của bot trong UI test có nút **Lưu câu này để bot tự trả lời lần sau**.

Nút không ghi thẳng nội dung chat. Nó mở một bước biên tập gồm câu hỏi tổng quát, câu trả lời tổng quát và checkbox xác nhận đã loại bỏ ngữ cảnh riêng. Bản nháp ban đầu tự che số điện thoại, email, tên tự khai; bỏ câu chào cá nhân, CTA gọi lại và yêu cầu thông tin liên hệ.

Trước khi lưu, validator tiếp tục chặn:

- số điện thoại, email hoặc tên khách;
- marker PII chưa được viết lại;
- nội dung rỗng;
- thao tác chưa đánh dấu xác nhận tổng quát hóa.

Sau khi hợp lệ, UI gọi đúng luồng H4-11 để tạo chunk `tenant_provided`, cập nhật index và hỏi lại xác minh trong dưới một phút. Nếu embedding/index lỗi, UI không tuyên bố đã lưu.

Các file chính:

- `app.py`: nút và form biên tập.
- `ingestion/chat_answer_save.py`: tổng quát hóa và validation.
- `ingestion/tenant_answer_ingestion.py`: nạp index và xác minh.
