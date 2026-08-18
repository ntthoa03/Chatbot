# H2-12 — Hướng dẫn Sale dùng thử chatbot

## Mục tiêu nghiệm thu

- Tối thiểu 2 Sale tham gia.
- Mỗi Sale thử tối thiểu 10 hội thoại thực tế trong tuần, đóng vai khách thật.
- Mỗi kịch bản mới cần bấm **Xoá hội thoại** để tạo một hội thoại mới; nếu không bấm thì hệ thống vẫn tính là cùng một hội thoại.
- Câu trả lời chưa tốt phải được báo ngay bằng nút **👎 Câu trả lời này tệ**.

## Cách mở

Người phụ trách chạy tại thư mục dự án:

```powershell
.\run_ui.ps1
```

- Máy chạy ứng dụng mở `http://localhost:8501`.
- Sale cùng mạng Wi-Fi mở `http://<IP-máy-chạy-app>:8501`.
- Lệnh `run_ui.ps1` mặc định chỉ nên dùng trong mạng nội bộ. Khi đưa ra Internet phải dùng
  `run_ui_ngrok.ps1`, script này bắt buộc đặt mã truy cập.

### Sale ở ngoài mạng — ngrok

Người phụ trách thực hiện một lần:

1. Tạo tài khoản tại `https://dashboard.ngrok.com/signup`.
2. Tải ngrok từ `https://ngrok.com/download`, giải nén và thêm `ngrok.exe` vào `PATH`.
3. Mở PowerShell và lưu authtoken bằng lệnh ngrok cung cấp trên dashboard:

```powershell
ngrok config add-authtoken <AUTHTOKEN-CỦA-BẠN>
```

Không gửi authtoken vào nhóm chat hoặc commit vào repo. Mỗi lần cần mở buổi test, chạy:

```powershell
cd "C:\Users\nghoa\Desktop\Chatbot - miama"
.\run_ui_ngrok.ps1
```

Script yêu cầu đặt mã truy cập tối thiểu 8 ký tự, bật Streamlit tại localhost và mở ngrok.
Gửi cho Sale URL HTTPS do ngrok hiển thị cùng mã truy cập qua kênh nội bộ. Giữ cửa sổ
PowerShell mở trong suốt buổi test; nhấn `Ctrl+C` để đóng tunnel.

## Sale thực hiện — hướng dẫn 10 phút

1. Mở link được gửi và nhập **Mã truy cập** (1 phút).
2. Nhập đúng một tên hoặc biệt danh cố định vào **Tên người test**, rồi chọn **Primary** (1 phút).
3. Đóng vai khách thật và hỏi tự nhiên: câu ngắn, sai chính tả hoặc không dấu đều được (5 phút).
4. Nếu reply không đúng, không hữu ích hoặc nói vòng vo, bấm **👎 Câu trả lời này tệ** ngay dưới reply đó.
5. Nhập câu trả lời mong muốn hoặc mô tả phần cần sửa, rồi bấm **Gửi feedback**. Nếu chưa biết đáp án đúng, ghi rõ `cần kiểm tra lại thông tin` và lý do thấy reply chưa tốt. Khi thấy mã feedback là đã gửi thành công, không cần nhắn riêng.
6. Bấm **Xoá hội thoại** trước khi chuyển sang một tình huống khách hàng mới (2 phút còn lại). Theo dõi chỉ số `x/10 hội thoại` trên sidebar và hoàn thành đủ 10 trong tuần.

Không nhập mật khẩu, OTP, số thẻ hoặc dữ liệu cá nhân thật không cần thiết.

## Người phụ trách kiểm tra dữ liệu đã nhận

```powershell
# Xem 20 lỗi mới nhất
python -m ai_core.feedback --tail 20

# Xem tiến độ: số Sale, số hội thoại, số lượt hỏi
python -m ai_core.feedback --stats

# Xuất CSV
python -m ai_core.feedback --export outputs\sale_feedback.csv
```

File gốc:

- `outputs/sale_feedback.jsonl`: chỉ các reply bị Sale đánh dấu tệ.
- `outputs/sale_ui_turns.jsonl`: mọi lượt hỏi thành công, dùng đo mức tham gia H2-12.

Đạt H2-12 khi lệnh `--stats` báo ít nhất 2 Sale, mỗi người đạt `10/10 hội thoại`, đồng thời các nút feedback đã được kiểm tra nhận thành công. Toàn bộ lượt hỏi thành công đều được ghi vào log, không phụ thuộc Sale có bấm feedback hay không.
