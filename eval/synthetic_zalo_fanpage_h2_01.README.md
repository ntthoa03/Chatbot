# Nguồn log tạm cho H2-01

`synthetic_zalo_fanpage_h2_01.jsonl` là dữ liệu **tự sinh**, không phải hội thoại khách hàng thật. File tồn tại để H2-01 không bị chặn trong lúc chờ bản xuất Zalo/Fanpage thật vào tuần sau.

Mỗi dòng có `synthetic=true`, kênh giả lập, thời gian giả lập, `topic`, `case_id` và nguyên văn tin nhắn. Câu hỏi được viết theo kiểu chat ngắn của khách: có viết tắt, không dấu, sai chính tả nhẹ, cách hỏi giá/tính năng trực tiếp và thiếu ngữ cảnh.

Sinh lại file bằng:

```powershell
python -m eval.generate_synthetic_logs
```

Khi có log thật:

1. Không ghi đè hoặc trộn âm thầm với file này.
2. Lưu log thật thành nguồn riêng và loại dữ liệu cá nhân trước khi đưa vào eval.
3. Thay dần case tổng hợp bằng câu thật nhưng giữ ID/version để so sánh baseline.
4. Ghi rõ case nào đến từ Zalo, Fanpage, Sale hoặc lỗi bot tuần 1.
