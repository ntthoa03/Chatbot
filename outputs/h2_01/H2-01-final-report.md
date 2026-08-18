# H2-01 — baseline eval 60 câu normal

Ngày chạy: 17/08/2026. Nguồn câu hỏi hiện là log Zalo/Fanpage tự sinh, được đánh dấu `synthetic=true`; sẽ thay bằng log thật khi nhận dữ liệu tuần sau.

## Kết quả normal

- Đúng: **43/60 — 71,67%**.
- Chi phí trung bình: **17,32 VND/hội thoại**.
- Độ trễ trung bình: **1.773,85 ms/hội thoại**.
- Lỗi hạ tầng: **0**.

| Chủ đề | Đạt | Tỷ lệ đúng | Chi phí TB (VND) | Độ trễ TB (ms) |
|---|---:|---:|---:|---:|
| Thiết kế website | 16/20 | 80,00% | 21,11 | 2.171,05 |
| SEO | 5/10 | 50,00% | 22,02 | 2.185,40 |
| Tên miền | 8/8 | 100,00% | 1,99 | 253,75 |
| Hosting/email | 5/7 | 71,43% | 18,48 | 2.093,00 |
| Quảng cáo | 4/8 | 50,00% | 23,01 | 2.194,75 |
| Quy trình/hợp đồng | 5/7 | 71,43% | 9,60 | 988,14 |
| **Tổng normal** | **43/60** | **71,67%** | **17,32** | **1.773,85** |

## Toàn bộ lượt chạy

Runner giữ lại 14 trap tuần 1 nên đã chạy tổng cộng 74 case. Kết quả toàn bộ là 49/74, tương đương 66,22%; chi phí trung bình 15,96 VND/lượt; độ trễ trung bình 1.680,73 ms; tổng chi phí 1.181,35 VND. Thời gian chạy 219,085 giây, đạt ngân sách 300 giây và không có ERROR.

Điểm 71,67% của normal tuần 2 không nên coi là tăng trực tiếp so với 70% tuần 1 vì bộ câu đã đổi từ 30 case hỗn hợp thành 60 normal có thêm bốn nhóm nghiệp vụ mới.

## Normal case chưa đạt

- Website: `H2N-W04`, `H2N-W08`, `H2N-W12`, `H2N-W13`.
- SEO: `Q015_ASCII`, `H2N-S02`, `H2N-S04`, `H2N-S06`, `H2N-S07`.
- Hosting/email: `H2N-H02`, `H2N-H06`.
- Quảng cáo: `H2N-A01`, `H2N-A05`, `H2N-A06`, `H2N-A07`.
- Quy trình/hợp đồng: `Q006`, `H2N-P03`.
- Tên miền: không có case sai.

Các lỗi này là đầu vào cho H2-02/H2-03 và cho việc điều chỉnh kho tri thức sau khi log thật về; không sửa tiêu chí chỉ để nâng điểm baseline.

## Tệp audit

- `20260817T045849.767071Z.json`: dữ liệu đầy đủ từng case.
- `20260817T045849.767071Z.csv`: bảng chi tiết từng câu, reply và topic.
- `20260817T045849.767071Z.topics.csv`: bảng điểm ngang theo chủ đề.
- `20260817T045849.767071Z.manual-review.csv`: câu hỏi, reply, chi phí và độ trễ để đánh giá thủ công.
